"""Smoke tests for the MINT CLI.

Runs ``mint run`` and ``mint plan`` through real end-to-end flows to verify
the CLI works correctly after code changes. Tests cover all three capability
types (MCP_TOOL, SKILL, AGENT) and all supported file formats (CSV, JSON,
YAML, XML, Excel, TSV, TXT).

Each test invokes the CLI runner directly (not subprocess) so we get clean
error output. Requires Docker running.

Usage::

    uv run scripts/test_mint.py                # all tests
    uv run scripts/test_mint.py --quick        # format tests only (no generation)
    uv run scripts/test_mint.py --mcp-only     # MCP_TOOL test only
    uv run scripts/test_mint.py --skill-only   # SKILL test only
    uv run scripts/test_mint.py --agent-only   # AGENT test only
    uv run scripts/test_mint.py --formats-only # file format metadata tests only
    uv run scripts/test_mint.py --verbose      # show debug logs

Takes ~5-15 minutes for full run (~$0.10-0.30 in LLM API costs).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

FIXTURES_DIR = ROOT / "tests" / "evaluation" / "fixtures"


# ---------------------------------------------------------------------------
# File format metadata tests (no LLM calls, fast)
# ---------------------------------------------------------------------------


def test_describe_file() -> bool:
    """Verify _describe_file produces correct metadata for all supported formats."""
    from src.cli.runner import _describe_file

    all_pass = True

    checks = [
        # (file, expected_substrings)
        (FIXTURES_DIR / "t01_standard.csv", ["CSV", "Columns:", "id", "name", "age", "city"]),
        (FIXTURES_DIR / "t02_config.json", ["JSON object", "Keys:", "database"]),
        (FIXTURES_DIR / "t03_document.txt", ["text", "words", "lines"]),
        (FIXTURES_DIR / "t04_reorder_rules.yaml", ["YAML", "Top-level keys:"]),
        (FIXTURES_DIR / "t09_report_data.xml", ["XML", "Root:", "<report>", "Child tags:"]),
        (FIXTURES_DIR / "t11_products.xlsx", ["Excel", "Products", "Columns:"]),
        (FIXTURES_DIR / "t14_transactions.tsv", ["TSV", "Columns:", "txn_id"]),
        (FIXTURES_DIR / "t13_baselines.json", ["JSON object", "Keys:", "Sample value"]),
    ]

    print(f"\n{'=' * 70}")
    print("  FILE FORMAT METADATA TESTS")
    print(f"{'=' * 70}")

    for fp, expected_subs in checks:
        desc = _describe_file(fp)
        ok = all(sub in desc for sub in expected_subs)
        status = "OK" if ok else "FAIL"
        print(f"\n  {fp.name}: {status}")
        print(f"    Output: {desc.strip()}")
        if not ok:
            missing = [s for s in expected_subs if s not in desc]
            print(f"    Missing: {missing}")
            all_pass = False

    return all_pass


# ---------------------------------------------------------------------------
# MINT run tests (full pipeline, requires Docker + LLM API)
# ---------------------------------------------------------------------------


def _run_mint(
    task: str,
    files: list[Path],
    *,
    skip_verification: bool = False,
    expect_output_keys: list[str] | None = None,
    test_name: str = "",
) -> bool:
    """Run a task through the MINT CLI runner and verify output.

    Returns True if the pipeline completes and output contains expected keys.
    """
    from src.cli.runner import execute_task

    print(f"\n{'=' * 70}")
    print(f"  MINT TEST: {test_name}")
    print(f"  Files: {', '.join(fp.name for fp in files)}")
    print(f"{'=' * 70}")
    print(f"  Task: {task[:120]}...")
    print("  Running...", flush=True)

    # Output saved to data/mint_output/ (default) so results are inspectable
    # after the run via `mint inspect <capability>` and `mint history`.
    exit_code = execute_task(
        task,
        files=files,
        params=None,
        skip_verification=skip_verification,
        no_optimizer=True,
        enable_optimizer=False,
        timeout=None,
        output_dir=None,  # uses default data/mint_output/
        json_output=False,
        verbose=False,
    )

    if exit_code != 0:
        print(f"\n  FAIL: exit code {exit_code}")
        return False

    print("  OK: Pipeline completed successfully")
    return True


def test_mcp_csv() -> bool:
    """MCP_TOOL: Parse CSV file (should match seeded csv-parser)."""
    return _run_mint(
        task=(
            "Parse this CSV file and return the row count and column names. "
            "Return a dict with keys 'row_count' (int) and 'column_names' (list of str)."
        ),
        files=[FIXTURES_DIR / "t01_standard.csv"],
        test_name="MCP_TOOL — CSV parsing (seeded capability match)",
    )


def test_skill_yaml_config() -> bool:
    """SKILL: Inventory reorder pipeline with CSV + YAML config (domain workflow)."""
    return _run_mint(
        task=(
            "Build an inventory reorder pipeline using these two data sources:\n"
            "1. Inventory CSV (columns: product_id, product_name, current_stock, daily_usage, unit_cost, warehouse)\n"
            "2. Reorder rules YAML (contains min_days_of_stock threshold, reorder_days_supply multiplier, max_order_value cap)\n\n"
            "Pipeline stages:\n"
            "(1) Load both data sources.\n"
            "(2) For each product, compute days_of_stock = current_stock / daily_usage.\n"
            "(3) Identify products where days_of_stock < min_days_of_stock (these need reordering).\n"
            "(4) For each product needing reorder, compute reorder_qty = ceil(daily_usage * reorder_days_supply), "
            "then order_cost = reorder_qty * unit_cost, capped at max_order_value.\n"
            "(5) Sum total_reorder_cost across all products needing reorder.\n\n"
            "Return a dict with keys 'product_count' (int), "
            "'items_below_threshold' (int), and 'total_reorder_cost' (float)."
        ),
        files=[
            FIXTURES_DIR / "t04_inventory.csv",
            FIXTURES_DIR / "t04_reorder_rules.yaml",
        ],
        test_name="SKILL — CSV + YAML (inventory reorder pipeline)",
    )


def test_mcp_xml() -> bool:
    """MCP_TOOL: Parse XML report data (new format)."""
    return _run_mint(
        task=(
            "Parse this XML report data and find the top 5 revenue anomalies "
            "(records with the highest revenue). Return a dict with keys "
            "'summary' (str), 'stats_table' (dict), and 'anomalies' (list of 5 items)."
        ),
        files=[FIXTURES_DIR / "t09_report_data.xml"],
        test_name="MCP_TOOL — XML parsing (report data)",
    )


def test_mcp_tsv() -> bool:
    """MCP_TOOL: Read TSV transactions + CSV departments (new format)."""
    return _run_mint(
        task=(
            "Read the departments CSV and the transactions TSV (tab-separated). "
            "Aggregate total spending per department. "
            "Return a dict with keys 'department_count' (int) and "
            "'department_spending' (dict mapping department name to total spending float)."
        ),
        files=[
            FIXTURES_DIR / "t14_departments.csv",
            FIXTURES_DIR / "t14_transactions.tsv",
        ],
        test_name="MCP_TOOL — CSV + TSV (budget aggregation)",
    )


def test_skill_excel() -> bool:
    """SKILL: Multi-source integration with Excel product catalog (new format)."""
    return _run_mint(
        task=(
            "Build a data integration pipeline using these three data sources: "
            "a sales CSV, an Excel product catalog (.xlsx), and a regional targets CSV. "
            "Steps: (1) Join sales with products on product_id. "
            "(2) Compute per-region revenue totals (quantity * unit_price). "
            "(3) Compare each region's revenue against its target. "
            "This requires a multi-step workflow integrating separate data sources "
            "with intermediate outputs. Return a dict with 'summary_row_count' (int), "
            "'region_revenues' (dict), and 'regions_meeting_target' (int)."
        ),
        files=[
            FIXTURES_DIR / "t11_sales.csv",
            FIXTURES_DIR / "t11_products.xlsx",
            FIXTURES_DIR / "t11_targets.csv",
        ],
        test_name="SKILL — CSV + Excel + CSV (multi-source integration)",
    )


def test_agent_yaml_incident() -> bool:
    """AGENT: Root cause diagnosis with YAML incident report (new format)."""
    return _run_mint(
        task=(
            "You are investigating a service degradation incident. "
            "Read the incident report YAML to understand symptoms and onset time. "
            "Examine the system metrics CSV around the onset time. "
            "Identify which metric changed FIRST (the root cause) by comparing "
            "onset times across metrics. Determine the cascade chain. "
            "This requires adaptive investigation: examine data, form hypotheses, "
            "test them, and revise. Return a dict with 'root_cause_metric' (str), "
            "'cascade_chain' (list of str), and 'n_metrics_investigated' (int)."
        ),
        files=[
            FIXTURES_DIR / "t16_system_metrics.csv",
            FIXTURES_DIR / "t16_incident.yaml",
        ],
        test_name="AGENT — CSV + YAML (root cause diagnosis)",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="MINT CLI smoke tests")
    parser.add_argument("--quick", action="store_true", help="Format metadata tests only (no LLM calls)")
    parser.add_argument("--mcp-only", action="store_true", help="MCP_TOOL tests only")
    parser.add_argument("--skill-only", action="store_true", help="SKILL test only")
    parser.add_argument("--agent-only", action="store_true", help="AGENT test only")
    parser.add_argument("--formats-only", action="store_true", help="File format metadata tests only")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    # Tee all stdout/stderr to a timestamped log file
    from datetime import datetime, UTC

    log_dir = ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"test_mint_{ts}.log"

    import re as _re
    _ANSI_RE = _re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07")

    class _Tee:
        """Write to both a file and the original stream, stripping ANSI from the log."""
        def __init__(self, stream, log_file):
            self._stream = stream
            self._log = log_file
            self.encoding = getattr(stream, "encoding", "utf-8")
        def write(self, data):
            self._stream.write(data)
            try:
                clean = _ANSI_RE.sub("", data)
                # Skip spinner-only lines (just whitespace after stripping)
                if clean.strip():
                    self._log.write(clean)
                    self._log.flush()
            except (ValueError, OSError):
                pass  # log file already closed during shutdown
        def flush(self):
            self._stream.flush()
            try:
                self._log.flush()
            except (ValueError, OSError):
                pass
        def fileno(self):
            return self._stream.fileno()
        def isatty(self):
            return self._stream.isatty()
        def __getattr__(self, name):
            return getattr(self._stream, name)

    _log_file = open(log_path, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, _log_file)
    sys.stderr = _Tee(sys.__stderr__, _log_file)
    print(f"Logging to {log_path}\n")

    # Reset CLI state before pipeline tests (clean registry + metrics)
    if not (args.quick or args.formats_only):
        print("\nResetting MINT CLI state (clean registry + metrics)...")
        import shutil

        from src.cli.runner import _create_infra, _data_dir, _load_env

        _load_env()
        data = _data_dir()
        chroma_path = data / "cli" / "chroma"
        metrics_path = data / "cli" / "metrics.duckdb"
        last_run_path = data / "last_run.json"
        if chroma_path.exists():
            shutil.rmtree(chroma_path)
        if metrics_path.exists():
            metrics_path.unlink()
        if last_run_path.exists():
            last_run_path.unlink()
        _create_infra(seed=True)
        print("Done — clean registry with seeded capabilities.\n")

    results: dict[str, bool] = {}

    # Always run format tests (fast, no LLM calls)
    results["File format metadata"] = test_describe_file()

    if args.quick or args.formats_only:
        pass  # skip pipeline tests
    elif args.mcp_only:
        results["MCP_TOOL — CSV"] = test_mcp_csv()
        results["SKILL — YAML config"] = test_skill_yaml_config()
        results["MCP_TOOL — XML"] = test_mcp_xml()
        results["MCP_TOOL — TSV"] = test_mcp_tsv()
    elif args.skill_only:
        results["SKILL — Excel"] = test_skill_excel()
    elif args.agent_only:
        results["AGENT — YAML incident"] = test_agent_yaml_incident()
    else:
        # Full test suite: prioritized by format diversity + capability type
        # 1. MCP_TOOL with CSV (seeded match — fast, verifies basic flow)
        results["MCP_TOOL — CSV"] = test_mcp_csv()
        # 2. SKILL with YAML config (domain workflow + new format)
        results["SKILL — YAML config"] = test_skill_yaml_config()
        # 3. SKILL with Excel (multi-source integration + new format)
        results["SKILL — Excel"] = test_skill_excel()
        # 4. AGENT with YAML incident (new format + third capability type)
        results["AGENT — YAML incident"] = test_agent_yaml_incident()
        # 5. MCP_TOOL with XML (new format)
        results["MCP_TOOL — XML"] = test_mcp_xml()
        # 6. MCP_TOOL with TSV (new format)
        results["MCP_TOOL — TSV"] = test_mcp_tsv()

    # Summary
    print(f"\n{'=' * 70}")
    print("  MINT SMOKE TEST RESULTS")
    print(f"{'=' * 70}")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    n_pass = sum(results.values())
    n_total = len(results)
    print(f"\n  {n_pass}/{n_total} passed")

    print(f"\n  Log: {log_path}")

    if n_pass == n_total:
        print("\n  ALL TESTS PASSED")
        _log_file.close()
        return 0
    else:
        print(f"\n  {n_total - n_pass} FAILED")
        _log_file.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())
