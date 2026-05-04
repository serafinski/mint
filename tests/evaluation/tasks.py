"""Canonical task definitions for the evaluation testbed.

Each TaskDefinition bundles:
- metadata (id, tier, static coverage)
- fixture paths consumed by the pipeline
- a validator callable (output_in_range check)
- a task_prompt fed to the JIT pipeline

Validators are pure functions: (output: dict) -> bool.
They read expected_values.json once at import time.

Reuse clusters
--------------
CSV cluster  : T01, T04, T05, T07  (tabular ingestion variants)
Analytics    : T08, T09             (T09 can reuse T08's cleaned output)

Pilot tasks: T01 (seeded retrieval), T07 (moderate JIT), T05 (moderate JIT,
static coverage).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from src.schemas.task import OutputContract

logger = logging.getLogger(__name__)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
_EV_PATH = FIXTURES_DIR / "expected_values.json"

# Tasks whose validators check metadata/shape only and do not fully cross-check
# output content against fixture ground truth. The disclosure is preserved for
# interpretation: all evaluated conditions used the same validators, so this is
# a uniform permissiveness limitation rather than a condition-specific bias.
# T05, T09, T10, and T13 were tightened; the rest remain permissive by design
# because extra tightening had low eval-integrity payoff relative to cost.
_PERMISSIVE_VALIDATOR_TASK_IDS: frozenset[str] = frozenset({
    "T02",  # errors list length only
    "T08",  # n_rows/n_columns shape + quality_issues_found > 0 (trivially met)
    "T12",  # clean_row_count exact + violations > 0 (count-based)
    "T15",  # n_days/metrics_aggregated/alert_days exact (but metadata only)
    "T16",  # root_cause_metric exact + cascade_chain length >= 2 (structural)
})

_PERMISSIVE_WARN_EMITTED = False


def warn_permissive_validators_once() -> None:
    """Emit a one-time WARN at harness startup naming permissive validators.

    Called by ``EvaluationHarness.__init__``. Idempotent.
    """
    global _PERMISSIVE_WARN_EMITTED
    if _PERMISSIVE_WARN_EMITTED:
        return
    _PERMISSIVE_WARN_EMITTED = True
    if not _PERMISSIVE_VALIDATOR_TASK_IDS:
        return
    ids = ", ".join(sorted(_PERMISSIVE_VALIDATOR_TASK_IDS))
    logger.warning(
        "The following testbed tasks rely on metadata-only validators "
        "(no fixture-content cross-check); success rates are permissive and the "
        "uniform-validator limitation must be disclosed: %s",
        ids,
    )

# Load expected values at import time — generate.py must have been run first.
if not _EV_PATH.exists():
    raise FileNotFoundError(
        f"expected_values.json not found at {_EV_PATH}. "
        "Run `python -m tests.evaluation.fixtures.generate` first."
    )

_EV: dict = json.loads(_EV_PATH.read_text())


@dataclass(frozen=True)
class TaskDefinition:
    """Immutable definition of one testbed task."""

    task_id: str
    tier: Literal["trivial", "moderate", "complex"]
    description: str
    task_prompt: str
    has_static_coverage: bool
    static_capability_name: str | None  # seeded registry entry that serves this task
    fixture_paths: list[Path] = field(default_factory=list)
    # Semantic kwargs passed to the generated tool beyond fixture file paths.
    # Covers cases where the LLM parameterizes values the task prompt specifies.
    extra_inputs: dict = field(default_factory=dict)
    # validator: (pipeline_output_dict) -> bool
    validator: Callable[[dict], bool] = field(default=lambda _: False, compare=False)
    # file_validator: (pipeline_output_dict, output_files) -> bool
    # Used for tasks that produce non-JSON file outputs (CSV, Excel, Markdown).
    # If set, the harness calls this instead of validator when output_files is non-empty.
    file_validator: Callable[[dict, dict[str, bytes]], bool] | None = field(
        default=None, compare=False,
    )
    # Declared output shape: first-class typed contract counterpart to the
    # prose task_prompt and bespoke validator closures. The harness copies this
    # directly into TaskState.output_contract at task entry and uses it for the
    # terminal output-shape check.
    output_contract: OutputContract = field(default_factory=OutputContract)
    # Author-assigned ground-truth primary capability type for task-level
    # classification-accuracy analysis. The analyzer cross-tabs this against
    # evaluation_runs.capability_type. Assignments are judgment calls made by
    # reading task_prompt and identifying the "core work" type.
    expected_type: Literal["mcp_tool", "skill", "agent"] | None = None
    # Recurrence-track identity. Breadth tasks leave these at their defaults.
    # Recurrence task instances keep task_id stable as the canonical family key
    # (e.g. "T11") and thread the variantized identity through the dedicated
    # fields below instead of inventing synthetic task IDs like "T11_V01".
    task_family_id: str | None = None
    task_instance_id: str | None = None
    variant_id: str | None = None
    benchmark_track: Literal["breadth", "recurrence"] = "breadth"

    @property
    def family_id(self) -> str:
        """Stable family-level task key used across breadth/recurrence."""
        return self.task_family_id or self.task_id

    @property
    def instance_id(self) -> str:
        """Concrete task-instance key; equals task_id for breadth runs."""
        return self.task_instance_id or self.task_id


# ---------------------------------------------------------------------------
# Validator helpers
# ---------------------------------------------------------------------------


def _check_keys_present(output: dict, keys: list[str]) -> bool:
    return all(k in output for k in keys)


def _within_pct(value: float, reference: float, pct: float) -> bool:
    if reference == 0:
        return abs(value) < 1e-9
    return abs(value - reference) / abs(reference) <= pct / 100.0


# ---------------------------------------------------------------------------
# T01 — Standard comma-delimited CSV
# ---------------------------------------------------------------------------

_t01_ev = _EV["T01"]


def _validate_t01(output: dict) -> bool:
    """row_count == expected AND column_names match."""
    try:
        ok_count = int(output.get("row_count", -1)) == _t01_ev["row_count"]
        returned_cols = set(output.get("column_names", []))
        ok_cols = returned_cols == set(_t01_ev["column_names"])
        return ok_count and ok_cols
    except (TypeError, ValueError):
        return False


T01 = TaskDefinition(
    task_id="T01",
    tier="trivial",
    description="Parse a standard comma-delimited CSV, return row count and column names",
    task_prompt=(
        "Parse the CSV file at tests/evaluation/fixtures/t01_standard.csv. "
        "Return a dict with keys 'row_count' (int) and 'column_names' (list of str)."
    ),
    has_static_coverage=True,
    static_capability_name="csv-parser",
    fixture_paths=[FIXTURES_DIR / "t01_standard.csv"],
    validator=_validate_t01,
    output_contract=OutputContract(required_keys=["row_count", "column_names"]),
)

# ---------------------------------------------------------------------------
# T02 — JSON config validation
# ---------------------------------------------------------------------------

_t02_ev = _EV["T02"]


def _validate_t02(output: dict) -> bool:
    """errors list must have exactly the right count (deterministic)."""
    try:
        errors = output.get("errors", [])
        return len(errors) == _t02_ev["expected_error_count"]
    except (TypeError, ValueError):
        return False


T02 = TaskDefinition(
    task_id="T02",
    tier="trivial",
    description=(
        "Validate a JSON config against a JSON Schema and return the list of "
        "validation errors"
    ),
    task_prompt=(
        "Validate the JSON file at tests/evaluation/fixtures/t02_config.json "
        "against the schema at tests/evaluation/fixtures/t02_schema.json. "
        "Return a dict with key 'errors' (list of str describing each violation)."
    ),
    has_static_coverage=True,
    static_capability_name="json-validator",
    fixture_paths=[FIXTURES_DIR / "t02_config.json", FIXTURES_DIR / "t02_schema.json"],
    validator=_validate_t02,
    output_contract=OutputContract(required_keys=["errors"]),
)

# ---------------------------------------------------------------------------
# T03 — Text statistics
# ---------------------------------------------------------------------------

_t03_ev = _EV["T03"]


def _validate_t03(output: dict) -> bool:
    """word_count within ±2% AND readability_score in [0, 100]."""
    try:
        wc = int(output.get("word_count", -1))
        rs = float(output.get("readability_score", -1))
        ok_wc = _within_pct(wc, _t03_ev["word_count"], _t03_ev["word_count_tolerance_pct"])
        ok_rs = 0 <= rs <= 100
        return ok_wc and ok_rs
    except (TypeError, ValueError):
        return False


T03 = TaskDefinition(
    task_id="T03",
    tier="trivial",
    description="Compute word count and Flesch readability score for a text document",
    task_prompt=(
        "Compute text statistics for the document at tests/evaluation/fixtures/t03_document.txt. "
        "Return a dict with keys 'word_count' (int) and 'readability_score' (float in 0–100)."
    ),
    has_static_coverage=False,
    static_capability_name=None,
    fixture_paths=[FIXTURES_DIR / "t03_document.txt"],
    validator=_validate_t03,
    output_contract=OutputContract(required_keys=["word_count", "readability_score"]),
)

# ---------------------------------------------------------------------------
# T04 — Inventory reorder pipeline (targets SKILL)
# ---------------------------------------------------------------------------

_t04_ev = _EV["T04"]


def _validate_t04(output: dict) -> bool:
    """product_count matches AND items_below_threshold within ±2 AND cost within ±20%."""
    try:
        ok_count = int(output.get("product_count", -1)) == _t04_ev["product_count"]
        actual_below = int(output.get("items_below_threshold", -1))
        ok_below = abs(actual_below - _t04_ev["items_below_threshold"]) <= 2
        actual_cost = float(output.get("total_reorder_cost", -1))
        expected_cost = _t04_ev["total_reorder_cost"]
        ok_cost = abs(actual_cost - expected_cost) / max(abs(expected_cost), 1) < 0.20
        return ok_count and ok_below and ok_cost
    except (TypeError, ValueError):
        return False


T04 = TaskDefinition(
    task_id="T04",
    tier="complex",
    description=(
        "Inventory reorder pipeline: apply domain-specific reorder policy from "
        "YAML rules to inventory data"
    ),
    task_prompt=(
        "Apply the inventory reorder policy defined in the rules file to the inventory data.\n\n"
        "The reorder policy is a domain-specific workflow:\n"
        "- The YAML rules at tests/evaluation/fixtures/t04_reorder_rules.yaml encode the business "
        "policy (min_days_of_stock threshold, reorder_days_supply multiplier, "
        "max_order_value cap).\n"
        "- The inventory CSV at tests/evaluation/fixtures/t04_inventory.csv "
        "contains the stock data "
        "(columns: product_id, product_name, current_stock, daily_usage, unit_cost, warehouse).\n\n"
        "The policy workflow is:\n"
        "(1) Load both data sources.\n"
        "(2) For each product, compute days_of_stock = current_stock / daily_usage.\n"
        "(3) Apply the policy rule: products where days_of_stock < "
        "min_days_of_stock need reordering.\n"
        "(4) For each product needing reorder, apply the reorder formula: "
        "reorder_qty = ceil(daily_usage * reorder_days_supply), "
        "then order_cost = reorder_qty * unit_cost, capped at max_order_value per the policy.\n"
        "(5) Sum total_reorder_cost across all products needing reorder.\n\n"
        "Return a dict with keys:\n"
        "- 'product_count' (int): total number of products\n"
        "- 'items_below_threshold' (int): number of products needing reorder\n"
        "- 'total_reorder_cost' (float): sum of capped order costs"
    ),
    has_static_coverage=True,
    static_capability_name="inventory-reorder-policy",
    fixture_paths=[
        FIXTURES_DIR / "t04_inventory.csv",
        FIXTURES_DIR / "t04_reorder_rules.yaml",
    ],
    validator=_validate_t04,
    output_contract=OutputContract(
        required_keys=["product_count", "items_below_threshold", "total_reorder_cost"],
    ),
)

# ---------------------------------------------------------------------------
# T05 — Data cleaning workflow
# ---------------------------------------------------------------------------

_t05_ev = _EV["T05"]


def _validate_t05(output: dict) -> bool:
    """output_row_count in a plausible cleaned-range AND null_count == 0.

    A simple ``out_count < input_row_count AND null_count == 0`` predicate accepts
    hallucinated outputs such as ``{output_row_count: 1, null_count: 0}``, so this
    requires the output count to fall inside a plausible range:
    ``expected_max_output_row_count * 0.5 <= out_count <= expected_max_output_row_count``.
    The max is the upper-bound ground-truth (dedup + null drop) from the fixture
    generator; the 50% floor rejects severely-truncated hallucinations while
    tolerating validator variance in interpretation of "null or empty".
    """
    try:
        out_count = int(output.get("output_row_count", -1))
        null_count = int(output.get("null_count", -1))
        max_expected = int(_t05_ev["expected_max_output_row_count"])
        min_expected = max_expected // 2  # 50% floor for truncated outputs
        ok_reduced = out_count < _t05_ev["input_row_count"]
        ok_range = min_expected <= out_count <= max_expected
        ok_nulls = null_count == 0
        return ok_reduced and ok_range and ok_nulls
    except (TypeError, ValueError):
        return False


T05 = TaskDefinition(
    task_id="T05",
    tier="moderate",
    description="Clean a CSV with nulls and duplicates, return cleaned row count and null count",
    task_prompt=(
        "Clean the dataset at tests/evaluation/fixtures/t05_dirty.csv. "
        "Remove duplicate rows and rows with any null/empty values. "
        "Return a dict with keys 'output_row_count' (int) and 'null_count' (int)."
    ),
    has_static_coverage=True,
    static_capability_name="data-cleaning-workflow",
    fixture_paths=[FIXTURES_DIR / "t05_dirty.csv"],
    validator=_validate_t05,
    output_contract=OutputContract(required_keys=["output_row_count", "null_count"]),
)

# ---------------------------------------------------------------------------
# T06 — KNN imputation (JIT)
# ---------------------------------------------------------------------------

_t06_ev = _EV["T06"]


def _validate_t06(output: dict) -> bool:
    """null_count == 0 AND variance of each column within ±10% of reference.

    Reference variances are post-KNN-imputation (population, ddof=0),
    computed by the fixture generator using a reference KNN implementation.
    This accounts for the inherent variance shrinkage of KNN imputation.
    """
    try:
        if int(output.get("null_count", -1)) != 0:
            return False
        variances = output.get("column_variances", {})
        tol = _t06_ev["variance_tolerance_pct"]
        # Support both old ("original_variances") and new ("expected_variances") keys
        ref_variances = _t06_ev.get("expected_variances") or _t06_ev.get("original_variances", {})
        for col, ref_var in ref_variances.items():
            returned = variances.get(col)
            if returned is None:
                return False
            if not _within_pct(float(returned), ref_var, tol):
                return False
        return True
    except (TypeError, ValueError, AttributeError):
        return False


T06 = TaskDefinition(
    task_id="T06",
    tier="moderate",
    description="KNN imputation on a dataset with ~15% missing values",
    task_prompt=(
        "Perform KNN imputation (k=5) on the dataset at "
        "tests/evaluation/fixtures/t06_missing_values.csv. "
        "Return a dict with keys 'null_count' (int, should be 0 after imputation) "
        "and 'column_variances' (dict mapping column name to float variance)."
    ),
    has_static_coverage=False,
    static_capability_name=None,
    fixture_paths=[FIXTURES_DIR / "t06_missing_values.csv"],
    extra_inputs={
        "n_neighbors": 5,
        "k": 5,
    },
    validator=_validate_t06,
    output_contract=OutputContract(required_keys=["null_count", "column_variances"]),
)

# ---------------------------------------------------------------------------
# T07 — Fuzzy join (JIT, PILOT task)
# ---------------------------------------------------------------------------

_t07_ev = _EV["T07"]


def _validate_t07(output: dict) -> bool:
    """len(df) == expected_join_count AND null_count_on_key == 0."""
    try:
        ok_count = int(output.get("row_count", -1)) == _t07_ev["expected_join_count"]
        ok_nulls = int(output.get("null_count_on_key", -1)) == 0
        return ok_count and ok_nulls
    except (TypeError, ValueError):
        return False


def _file_validate_t07(output: dict, output_files: dict[str, bytes]) -> bool:
    """CSV file has expected_join_count data rows AND row_count in metadata.

    T07's file validator is only reached when ``output_contract.required_files``
    is non-empty, so a missing ``output.csv`` is a contract violation, not a
    graceful-degradation case. Fail closed on missing or undecodable files.
    """
    import csv
    import io
    try:
        # Check metadata dict
        ok_count = int(output.get("row_count", -1)) == _t07_ev["expected_join_count"]
        # Check CSV file content — required by T07's output_contract.
        csv_bytes = output_files.get("output.csv")
        if csv_bytes is None:
            # Pre-fix fell back to dict-only; this is a contract
            # violation and the pipeline never produced the required
            # file. Fail closed.
            return False
        text = csv_bytes.decode("utf-8")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        # First row is header, rest are data
        data_rows = len(rows) - 1 if len(rows) > 1 else 0
        ok_file_rows = data_rows == _t07_ev["expected_join_count"]
        return ok_count and ok_file_rows
    except (TypeError, ValueError, UnicodeDecodeError, csv.Error):
        return False


T07 = TaskDefinition(
    task_id="T07",
    tier="moderate",
    description="Fuzzy join two datasets on company_name, output joined data as CSV file",
    task_prompt=(
        "Perform a fuzzy join between "
        "tests/evaluation/fixtures/t07_left.csv (orders) and "
        "tests/evaluation/fixtures/t07_right.csv (companies) "
        "on the 'company_name' column. Before matching: normalise case, strip whitespace, "
        "and strip common business suffixes (Inc, Ltd, Co, LLC).\n\n"
        "Output the joined dataset as a CSV file (write it to /workspace/output.csv). "
        "Also return a dict with keys 'row_count' (int, matched join rows), "
        "'null_count_on_key' (int, null company_name values in result), "
        "and 'output_file' (str, 'output.csv')."
    ),
    has_static_coverage=False,
    static_capability_name=None,
    fixture_paths=[FIXTURES_DIR / "t07_left.csv", FIXTURES_DIR / "t07_right.csv"],
    validator=_validate_t07,
    file_validator=_file_validate_t07,
    output_contract=OutputContract(
        required_keys=["row_count", "null_count_on_key", "output_file"],
        required_files=["output.csv"],
    ),
)

# ---------------------------------------------------------------------------
# T08 — Data quality investigation agent (targets AGENT)
# ---------------------------------------------------------------------------

_t08_ev = _EV["T08"]


def _validate_t08(output: dict) -> bool:
    """quality_issues_found > 0 AND n_rows matches AND n_columns matches."""
    try:
        actual_issues = int(output.get("quality_issues_found", 0))
        ok_issues = actual_issues > 0  # agent should find at least some issues
        ok_rows = int(output.get("n_rows", -1)) == _t08_ev["n_rows"]
        ok_cols = int(output.get("n_columns", -1)) == _t08_ev["n_columns"]
        return ok_issues and ok_rows and ok_cols
    except (TypeError, ValueError):
        return False


T08 = TaskDefinition(
    task_id="T08",
    tier="complex",
    description=(
        "Investigate data quality issues iteratively: profile, hypothesize root "
        "causes, verify, and prioritize"
    ),
    task_prompt=(
        "You are investigating data quality issues in a messy employee dataset.\n"
        "Dataset: tests/evaluation/fixtures/t08_messy_data.csv "
        "(columns: id, name, age, salary, department, join_date)\n\n"
        "Conduct an iterative investigation:\n"
        "(1) Load and profile the dataset — check shape, dtypes, null counts per column.\n"
        "(2) Based on the profile, form hypotheses about what patterns of issues exist "
        "(e.g., are nulls concentrated in certain departments? are salary outliers "
        "correlated with specific join_dates?).\n"
        "(3) Test each hypothesis by examining the relevant data subset. "
        "If a hypothesis is confirmed, investigate whether the root cause is "
        "a data entry problem, an import artifact, or a schema mismatch.\n"
        "(4) Categorize each issue type and count occurrences.\n"
        "(5) Prioritize issues based on frequency, downstream impact, and "
        "whether they are systematic (fixable with a rule) vs random.\n\n"
        "This requires adaptive reasoning — the investigation path depends on what "
        "each analysis step reveals. Do not pre-plan all checks; let findings guide "
        "the next analysis.\n\n"
        "Return a dict with keys:\n"
        "- 'n_rows' (int): total rows in the dataset\n"
        "- 'n_columns' (int): total columns\n"
        "- 'quality_issues_found' (int): total count of all quality issues detected\n"
        "- 'issue_categories' (list of str): names of issue types found "
        "(e.g., 'null_ages', 'salary_outliers', 'invalid_dates')"
    ),
    has_static_coverage=False,
    static_capability_name=None,
    fixture_paths=[FIXTURES_DIR / "t08_messy_data.csv"],
    validator=_validate_t08,
    output_contract=OutputContract(
        required_keys=["n_rows", "n_columns", "quality_issues_found", "issue_categories"],
    ),
)

# ---------------------------------------------------------------------------
# T09 — Report generation
# ---------------------------------------------------------------------------

_t09_ev = _EV["T09"]


def _validate_t09(output: dict) -> bool:
    """output has required keys AND anomalies list shape + content checks.

    Counting only ``len(anomalies) == N`` accepts fabricated stand-in strings
    such as ``["fake", "fake", "fake", "fake", "fake"]``. Each anomaly must
    be a dict or a string containing at least one digit, because real anomalies
    reference revenue/unit numbers from the XML, and entries must be unique.
    """
    try:
        if not _check_keys_present(output, _t09_ev["required_keys"]):
            return False
        anomalies = output.get("anomalies", [])
        if len(anomalies) != _t09_ev["expected_anomaly_count"]:
            return False
        seen: set[str] = set()
        for item in anomalies:
            if isinstance(item, dict):
                # Real structured anomaly — accept.
                key = json.dumps(item, sort_keys=True, default=str)
            elif isinstance(item, str):
                # String anomaly must reference a number (revenue/units/returns).
                if not any(ch.isdigit() for ch in item):
                    return False
                key = item
            else:
                return False
            if key in seen:
                return False
            seen.add(key)
        return True
    except (TypeError, ValueError):
        return False


T09 = TaskDefinition(
    task_id="T09",
    tier="complex",
    description="Generate structured report: summary section, stats table, and top-5 anomalies",
    task_prompt=(
        "Generate an analytics report from the XML data at "
        "tests/evaluation/fixtures/t09_report_data.xml. "
        "The XML has structure: <report><title>, <period start=... end=...>, "
        "<records> containing <record> elements with <region>, <product>, "
        "<revenue>, <units>, <returns> child elements. "
        "Parse the XML, extract the records, and identify the top 5 "
        "revenue anomalies (highest outliers). "
        "Return a dict with keys 'summary' (str), 'stats_table' (dict or list), "
        "and 'anomalies' (list of exactly 5 items)."
    ),
    has_static_coverage=True,
    static_capability_name="report-generation",
    fixture_paths=[FIXTURES_DIR / "t09_report_data.xml"],
    validator=_validate_t09,
    output_contract=OutputContract(required_keys=["summary", "stats_table", "anomalies"]),
)

# ---------------------------------------------------------------------------
# T10 — Trend detection and forecasting agent (targets AGENT)
# ---------------------------------------------------------------------------

_t10_ev = _EV["T10"]


def _validate_t10(output: dict) -> bool:
    """trend_direction upward AND n_weeks matches AND forecast is numeric + plausible.

    This rejects plausible-looking but fabricated forecasts by requiring exactly
    ``n_forecast_weeks`` numeric entries, rejecting constant forecasts, and
    requiring the forecast mean to sit within
    ``[avg_last_4_weeks * 0.3, avg_last_4_weeks * 3.0]``.
    """
    try:
        ok_trend = output.get("trend_direction", "").lower() in (
            "upward", "up", "increasing", "positive",
        )
        ok_weeks = int(output.get("n_weeks", -1)) == _t10_ev["n_weeks"]
        forecast = output.get("forecast")
        if not isinstance(forecast, list):
            return False
        n_expected = int(_t10_ev["n_forecast_weeks"])
        if len(forecast) != n_expected:
            return False
        try:
            values = [float(v) for v in forecast]
        except (TypeError, ValueError):
            return False
        if len(set(values)) == 1:
            # All-constant forecast (includes the [0, 0, 0] hallucination case).
            return False
        mean_forecast = sum(values) / len(values)
        recent_avg = float(_t10_ev["avg_last_4_weeks"])
        low, high = recent_avg * 0.3, recent_avg * 3.0
        ok_forecast = low <= mean_forecast <= high
        return ok_trend and ok_weeks and ok_forecast
    except (TypeError, ValueError):
        return False


T10 = TaskDefinition(
    task_id="T10",
    tier="complex",
    description=(
        "Adaptive trend analysis: try multiple forecasting methods, compare "
        "errors, select best with justification"
    ),
    task_prompt=(
        "You are analyzing weekly sales data to detect trends and make forecasts.\n"
        "Dataset: tests/evaluation/fixtures/t10_weekly_sales.csv "
        "(columns: week, sales, promotions, region)\n\n"
        "Conduct an adaptive analysis:\n"
        "(1) Load the data and examine the sales column over time.\n"
        "(2) Detect the overall trend direction (upward, downward, or flat) by "
        "comparing early weeks to later weeks.\n"
        "(3) Check for seasonality or cyclical patterns in the data.\n"
        "(4) Try at least two different forecasting methods (e.g., simple moving average, "
        "linear regression, exponential smoothing). For each method, compute the error "
        "on the last 4 known weeks (hold-out validation).\n"
        "(5) Select the method with the lowest error and use it to forecast the next 4 weeks. "
        "If the best method's error is still high, investigate whether the data has "
        "structural breaks or regime changes that explain the poor fit.\n\n"
        "This requires adaptive reasoning — the choice of method depends on what the data "
        "looks like, and you may need to revise your approach based on intermediate results.\n\n"
        "Return a dict with keys:\n"
        "- 'n_weeks' (int): number of weeks in the input data\n"
        "- 'trend_direction' (str): 'upward', 'downward', or 'flat'\n"
        "- 'has_seasonality' (bool): whether cyclical patterns were detected\n"
        "- 'forecast' (list of float): predicted sales for the next 4 weeks"
    ),
    has_static_coverage=False,
    static_capability_name=None,
    fixture_paths=[FIXTURES_DIR / "t10_weekly_sales.csv"],
    validator=_validate_t10,
    output_contract=OutputContract(
        required_keys=["n_weeks", "trend_direction", "has_seasonality", "forecast"],
    ),
)

# ---------------------------------------------------------------------------
# T11 — Multi-source data integration (targets SKILL)
# ---------------------------------------------------------------------------

_t11_ev = _EV["T11"]


def _validate_t11(output: dict) -> bool:
    """summary_row_count == 4 AND region revenues within ±5% AND regions_meeting_target exact."""
    try:
        if int(output.get("summary_row_count", -1)) != _t11_ev["summary_row_count"]:
            return False
        revenues = output.get("region_revenues", {})
        tol = _t11_ev["revenue_tolerance_pct"]
        for region, expected_rev in _t11_ev["region_revenues"].items():
            actual = revenues.get(region)
            if actual is None:
                return False
            if not _within_pct(float(actual), expected_rev, tol):
                return False
        ok_meeting = (
            int(output.get("regions_meeting_target", -1))
            == _t11_ev["regions_meeting_target"]
        )
        return ok_meeting
    except (TypeError, ValueError, AttributeError):
        return False


def _file_validate_t11(output: dict, output_files: dict[str, bytes]) -> bool:
    """Excel file exists with a 'Summary' sheet containing region revenue rows.

    Missing or corrupt files are contract violations. The only graceful
    degradation path is ``ImportError`` when a developer environment lacks
    openpyxl; parse failures remain invalid.
    """
    # Dict validation still required for metadata
    if not _validate_t11(output):
        return False
    # Check Excel file — required by T11's output_contract.
    xlsx_bytes = output_files.get("output.xlsx")
    if xlsx_bytes is None:
        # Pre-fix returned True; the file is contract-required.
        return False
    try:
        import io

        import openpyxl
    except ImportError:
        # Graceful degradation only when openpyxl is unavailable in this
        # dev env. openpyxl is a hard eval dependency in pyproject so
        # this branch fires only in narrow developer setups.
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "openpyxl unavailable; T11 falling back to dict-only validation."
        )
        return True
    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True)
        # Should have at least one sheet
        if len(wb.sheetnames) == 0:
            return False
        # Check first sheet has data rows (header + at least 4 region rows)
        ws = wb.active
        row_count = sum(1 for _ in ws.iter_rows(min_row=1))
        return row_count >= 5  # header + 4 regions
    except Exception:  # noqa: BLE001
        # Parse failures (BadZipFile, InvalidFileException, etc.) on a
        # file that WAS produced but is corrupt → contract violation.
        return False


T11 = TaskDefinition(
    task_id="T11",
    tier="complex",
    description=(
        "Multi-source data integration: join, compute regional revenue, output "
        "summary as Excel file"
    ),
    task_prompt=(
        "Build a data integration pipeline using these three data sources:\n"
        "1. Sales CSV at tests/evaluation/fixtures/t11_sales.csv (columns: "
        "order_id, product_id, quantity, unit_price, region)\n"
        "2. Product catalog Excel at tests/evaluation/fixtures/t11_products.xlsx "
        "(columns: product_id, name, category)\n"
        "3. Regional targets CSV at tests/evaluation/fixtures/t11_targets.csv "
        "(columns: region, target_revenue)\n\n"
        "Steps: (1) Join the sales data with the product catalog on product_id "
        "to enrich sales records with product names and categories. "
        "(2) Compute per-region revenue totals by summing quantity * unit_price for each region. "
        "(3) Compare each region's total revenue against its target from the targets CSV.\n\n"
        "Output the regional summary as an Excel file (write it to /workspace/output.xlsx) "
        "with columns: region, total_revenue, target_revenue, meets_target.\n\n"
        "Also return a dict with keys 'summary_row_count' (int, number of regions), "
        "'region_revenues' (dict mapping region name to float total revenue), "
        "'regions_meeting_target' (int, count of regions where revenue >= target), "
        "and 'output_file' (str, 'output.xlsx')."
    ),
    has_static_coverage=True,
    static_capability_name="sales-integration-report",
    fixture_paths=[
        FIXTURES_DIR / "t11_sales.csv",
        FIXTURES_DIR / "t11_products.xlsx",
        FIXTURES_DIR / "t11_targets.csv",
    ],
    validator=_validate_t11,
    file_validator=_file_validate_t11,
    output_contract=OutputContract(
        required_keys=[
            "summary_row_count",
            "region_revenues",
            "regions_meeting_target",
            "output_file",
        ],
        required_files=["output.xlsx"],
    ),
)

# ---------------------------------------------------------------------------
# T12 — Configurable data quality pipeline (targets SKILL)
# ---------------------------------------------------------------------------

_t12_ev = _EV["T12"]


def _validate_t12(output: dict) -> bool:
    """clean_row_count == expected AND violations_found > 0 AND rules_applied == 3."""
    try:
        ok_clean = int(output.get("clean_row_count", -1)) == _t12_ev["clean_row_count"]
        ok_violations = int(output.get("violations_found", -1)) > 0
        ok_rules = int(output.get("rules_applied", -1)) == _t12_ev["rules_applied"]
        return ok_clean and ok_violations and ok_rules
    except (TypeError, ValueError):
        return False


T12 = TaskDefinition(
    task_id="T12",
    tier="complex",
    description=(
        "Apply a domain-specific data quality policy (encoded as JSON rules) to "
        "enforce constraints on raw data"
    ),
    task_prompt=(
        "Apply the data quality policy defined in the rules configuration to the raw dataset.\n\n"
        "The quality policy is encoded as a JSON rules file at "
        "tests/evaluation/fixtures/t12_rules.json — this is the domain expertise that defines "
        "what 'clean data' means for this organization. The raw data CSV is at "
        "tests/evaluation/fixtures/t12_raw_data.csv (columns: id, name, age, "
        "score, department).\n\n"
        "The policy prescribes three sequential quality rules:\n"
        "(1) Non-null enforcement: drop rows where any column listed in "
        "'non_null_columns' is empty or null.\n"
        "(2) Range constraints: drop rows where values fall outside the min/max "
        "range specified in 'range_constraints'.\n"
        "(3) Deduplication: remove duplicate rows based on the columns listed "
        "in 'dedup_key_columns'.\n\n"
        "Apply these rules in the prescribed order — the sequencing matters because each rule "
        "filters the input for the next. "
        "Return a dict with keys 'clean_row_count' (int, rows remaining after all rules), "
        "'violations_found' (int, total rows removed across all steps), "
        "and 'rules_applied' (int, number of rule types that were applied)."
    ),
    has_static_coverage=False,
    static_capability_name=None,
    fixture_paths=[
        FIXTURES_DIR / "t12_raw_data.csv",
        FIXTURES_DIR / "t12_rules.json",
    ],
    validator=_validate_t12,
    output_contract=OutputContract(
        required_keys=["clean_row_count", "violations_found", "rules_applied"],
    ),
)

# ---------------------------------------------------------------------------
# T13 — Autonomous anomaly investigation (targets AGENT)
# ---------------------------------------------------------------------------

_t13_ev = _EV["T13"]


def _validate_t13(output: dict) -> bool:
    """anomaly_count >= 3 AND anomalies reference real metric names from the fixture.

    A non-empty anomalies list with ``anomaly_count >= 3`` is not enough: each
    anomaly must be a dict whose ``metric`` value is one of the four real metric
    names enumerated in the task prompt. A ``timestamp`` key is also required so
    count-only hallucinations cannot pass.
    """
    valid_metrics = {"cpu_usage", "memory_mb", "request_latency_ms", "error_rate"}
    try:
        anomaly_count = int(output.get("anomaly_count", 0))
        anomalies = output.get("anomalies", [])
        metrics_analyzed = int(output.get("metrics_analyzed", 0))
        if anomaly_count < 3 or metrics_analyzed <= 0 or len(anomalies) == 0:
            return False
        for item in anomalies:
            if not isinstance(item, dict):
                return False
            metric = item.get("metric")
            if metric not in valid_metrics:
                return False
            if "timestamp" not in item:
                return False
        return True
    except (TypeError, ValueError):
        return False


T13 = TaskDefinition(
    task_id="T13",
    tier="complex",
    description=(
        "Iterative anomaly investigation: detect anomalies, then investigate "
        "correlations and temporal clustering"
    ),
    task_prompt=(
        "Investigate anomalies in the metrics dataset. You have two data sources:\n"
        "1. Time-series metrics CSV at tests/evaluation/fixtures/t13_metrics.csv "
        "(columns: timestamp, cpu_usage, memory_mb, request_latency_ms, error_rate)\n"
        "2. Baselines JSON at tests/evaluation/fixtures/t13_baselines.json "
        "(normal ranges per metric with min/max values)\n\n"
        "Conduct an iterative investigation:\n"
        "(1) Profile the data to understand the distribution of each metric.\n"
        "(2) Detect anomalies by comparing each metric's values against the baselines "
        "(values outside the normal min/max range are anomalous).\n"
        "(3) For each anomaly found, identify the timestamp, metric name, and anomalous value.\n"
        "(4) After the initial detection, investigate further: if one metric has many anomalies, "
        "check whether they cluster in time (suggesting an incident window). If multiple "
        "metrics spike simultaneously, investigate whether they correlate (suggesting a "
        "common root cause). Let the findings from step (2) guide what you investigate next.\n\n"
        "This requires iterative reasoning — the investigation path depends on what "
        "the initial anomaly scan reveals.\n\n"
        "Return a dict with keys 'anomaly_count' (int, total anomalies detected), "
        "'anomalies' (list of dicts, each with keys 'timestamp', 'metric', 'value'), "
        "and 'metrics_analyzed' (int, number of unique metrics you examined)."
    ),
    has_static_coverage=True,
    static_capability_name="anomaly-investigation-agent",
    fixture_paths=[
        FIXTURES_DIR / "t13_metrics.csv",
        FIXTURES_DIR / "t13_baselines.json",
    ],
    validator=_validate_t13,
    output_contract=OutputContract(
        required_keys=["anomaly_count", "anomalies", "metrics_analyzed"],
    ),
)

# ---------------------------------------------------------------------------
# T14 — Budget reconciliation pipeline (targets SKILL)
# ---------------------------------------------------------------------------

_t14_ev = _EV["T14"]


def _validate_t14(output: dict) -> bool:
    """department_count correct AND spending, budget, and flag counts match."""
    try:
        if int(output.get("department_count", -1)) != _t14_ev["department_count"]:
            return False
        spending = output.get("department_spending", {})
        tol = _t14_ev["spending_tolerance_pct"]
        for dept, expected in _t14_ev["department_spending"].items():
            actual = spending.get(dept)
            if actual is None:
                return False
            if not _within_pct(float(actual), expected, tol):
                return False
        if int(output.get("over_budget_count", -1)) != _t14_ev["over_budget_count"]:
            return False
        if int(output.get("flagged_transactions", -1)) != _t14_ev["flagged_transactions"]:
            return False
        return True
    except (TypeError, ValueError, AttributeError):
        return False


T14 = TaskDefinition(
    task_id="T14",
    tier="complex",
    description=(
        "Apply budget allocation policy (encoded as JSON rules) to reconcile "
        "departmental spending against budgets"
    ),
    task_prompt=(
        "Apply the organization's budget allocation policy to reconcile departmental spending.\n\n"
        "The policy is encoded in three data sources:\n"
        "1. Department budgets CSV at tests/evaluation/fixtures/t14_departments.csv "
        "(columns: department, annual_budget) — the spending limits per department.\n"
        "2. Transactions TSV at tests/evaluation/fixtures/t14_transactions.tsv "
        "(tab-separated, columns: txn_id, department, amount, category, date) "
        "— the actual spending.\n"
        "3. Allocation rules JSON at tests/evaluation/fixtures/t14_rules.json "
        "(contains max_single_transaction threshold and category_caps as fraction of budget) — "
        "the compliance rules that define what spending patterns are flagged.\n\n"
        "The reconciliation policy workflow is:\n"
        "(1) Load all three data sources.\n"
        "(2) Aggregate total spending per department from the transactions.\n"
        "(3) Apply the budget compliance rule: departments where spending > "
        "annual_budget are over-budget.\n"
        "(4) Apply the transaction flag rule: individual transactions exceeding "
        "max_single_transaction from the rules are flagged for review.\n\n"
        "Return a dict with keys:\n"
        "- 'department_count' (int): number of departments\n"
        "- 'department_spending' (dict): mapping department name to float total spending\n"
        "- 'over_budget_count' (int): number of departments where spending > annual_budget\n"
        "- 'flagged_transactions' (int): count of transactions exceeding max_single_transaction"
    ),
    has_static_coverage=True,
    static_capability_name="budget-allocation-policy",
    fixture_paths=[
        FIXTURES_DIR / "t14_departments.csv",
        FIXTURES_DIR / "t14_transactions.tsv",
        FIXTURES_DIR / "t14_rules.json",
    ],
    validator=_validate_t14,
    output_contract=OutputContract(
        required_keys=[
            "department_count",
            "department_spending",
            "over_budget_count",
            "flagged_transactions",
        ],
    ),
)

# ---------------------------------------------------------------------------
# T15 — Time-series ETL with configurable aggregation (targets SKILL)
# ---------------------------------------------------------------------------

_t15_ev = _EV["T15"]


def _validate_t15(output: dict) -> bool:
    """n_days == 7 AND metrics_aggregated == 4 AND alert_days correct."""
    try:
        if int(output.get("n_days", -1)) != _t15_ev["n_days"]:
            return False
        if int(output.get("metrics_aggregated", -1)) != _t15_ev["metrics_aggregated"]:
            return False
        if int(output.get("alert_days", -1)) != _t15_ev["alert_days"]:
            return False
        return True
    except (TypeError, ValueError):
        return False


T15 = TaskDefinition(
    task_id="T15",
    tier="complex",
    description=(
        "Apply a config-driven monitoring policy to aggregate hourly metrics "
        "and detect threshold breaches"
    ),
    task_prompt=(
        "Apply the monitoring aggregation policy defined in the configuration "
        "to the raw metrics data.\n\n"
        "The policy is encoded in the aggregation config JSON at "
        "tests/evaluation/fixtures/t15_agg_config.json — it specifies the monitoring rules:\n"
        "- 'aggregation_interval': 'daily' — group hourly rows by date\n"
        "- 'metrics': dict mapping each metric name to a list of aggregation functions "
        "(e.g., 'cpu_pct': ['mean', 'max'] means compute daily mean and daily max of cpu_pct)\n"
        "- 'alert_thresholds': dict mapping metric+function to threshold values "
        "(e.g., 'cpu_pct_max': 85 means alert if any day's max cpu_pct > 85)\n\n"
        "The raw hourly metrics are at tests/evaluation/fixtures/t15_hourly_metrics.csv "
        "(columns: timestamp, cpu_pct, memory_pct, requests_per_sec, error_count).\n\n"
        "The monitoring policy workflow is:\n"
        "(1) Load the hourly metrics and parse timestamps.\n"
        "(2) Group rows by day per the policy's aggregation_interval.\n"
        "(3) For each day, compute the aggregations specified in the policy "
        "config for each metric.\n"
        "(4) Apply the policy's alert thresholds: count alert days "
        "(a day is an alert day if ANY of its aggregated values exceeds the "
        "corresponding threshold).\n\n"
        "Return a dict with keys:\n"
        "- 'n_days' (int): number of unique days in the data\n"
        "- 'metrics_aggregated' (int): number of distinct metrics processed\n"
        "- 'alert_days' (int): number of days where at least one threshold was breached"
    ),
    has_static_coverage=True,
    static_capability_name="monitoring-aggregation-policy",
    fixture_paths=[
        FIXTURES_DIR / "t15_hourly_metrics.csv",
        FIXTURES_DIR / "t15_agg_config.json",
    ],
    validator=_validate_t15,
    output_contract=OutputContract(
        required_keys=["n_days", "metrics_aggregated", "alert_days"],
    ),
)

# ---------------------------------------------------------------------------
# T16 — Root cause diagnosis (targets AGENT)
# ---------------------------------------------------------------------------

_t16_ev = _EV["T16"]


def _validate_t16(output: dict) -> bool:
    """root_cause_metric matches AND cascade_chain has >=2 items AND n_metrics_investigated > 0."""
    try:
        identified_root = output.get("root_cause_metric", "")
        if identified_root != _t16_ev["root_cause_metric"]:
            return False
        chain = output.get("cascade_chain", [])
        if not isinstance(chain, list) or len(chain) < 2:
            return False
        n_investigated = int(output.get("n_metrics_investigated", 0))
        if n_investigated < 2:
            return False
        return True
    except (TypeError, ValueError):
        return False


T16 = TaskDefinition(
    task_id="T16",
    tier="complex",
    description=(
        "Root cause diagnosis: investigate a cascading system failure, "
        "identify the root cause metric and the cascade chain"
    ),
    task_prompt=(
        "You are investigating a service degradation incident. You have:\n"
        "1. System metrics CSV at tests/evaluation/fixtures/t16_system_metrics.csv "
        "(columns: timestamp, db_connections, query_latency_ms, "
        "request_queue_depth, error_rate, cpu_usage)\n"
        "2. Incident report YAML at tests/evaluation/fixtures/t16_incident.yaml "
        "(contains title, symptoms, onset_time, affected_service)\n\n"
        "The incident involves a cascading failure where one metric spiked first and "
        "caused other metrics to degrade in sequence. Your task is to:\n\n"
        "(1) Read the incident report to extract the symptoms and the incident onset_time.\n"
        "(2) Use the incident onset_time as the anchor for your investigation. Treat "
        "all metric values BEFORE the onset_time as the baseline (normal) period. "
        "Treat the period AT or AFTER the onset_time as the incident window. Do NOT "
        "flag pre-onset random fluctuations as anomalies — those are baseline noise.\n"
        "(3) Identify which metric changed FIRST within the incident window (at or "
        "after onset_time). The root cause metric is the metric whose first sustained "
        "deviation from its baseline occurs at the earliest timestamp >= onset_time.\n"
        "(4) Determine the cascade chain — the order in which other metrics were affected "
        "after the root cause, using only timestamps >= onset_time. Look for staggered "
        "onset patterns.\n"
        "(5) Note: not all metrics are part of the cascade. Some may remain normal "
        "throughout the incident (red herrings).\n\n"
        "This requires adaptive investigation: examine the data, form hypotheses about "
        "which metric is the root cause, test those hypotheses by checking temporal "
        "ordering, and revise if the evidence doesn't support your initial theory.\n\n"
        "Return a dict with keys:\n"
        "- 'root_cause_metric' (str): the name of the metric whose first sustained "
        "deviation from baseline occurs at the earliest timestamp >= onset_time\n"
        "- 'cascade_chain' (list of str): ordered list of metrics in the cascade "
        "(starting with the root cause, then each subsequent metric in order of onset "
        "within the incident window)\n"
        "- 'n_metrics_investigated' (int): how many distinct metrics you examined"
    ),
    has_static_coverage=False,
    static_capability_name=None,
    fixture_paths=[
        FIXTURES_DIR / "t16_system_metrics.csv",
        FIXTURES_DIR / "t16_incident.yaml",
    ],
    validator=_validate_t16,
    output_contract=OutputContract(
        required_keys=["root_cause_metric", "cascade_chain", "n_metrics_investigated"],
    ),
)

# ---------------------------------------------------------------------------
# T17 — Fraud investigation with cross-referencing (targets AGENT)
# ---------------------------------------------------------------------------

_t17_ev = _EV["T17"]


def _validate_t17(output: dict) -> bool:
    """total/high-severity alert counts match AND at least 2 fraud customers match."""
    try:
        if int(output.get("total_alerts", -1)) != _t17_ev["total_alerts"]:
            return False
        if int(output.get("high_severity_alerts", -1)) != _t17_ev["high_severity_alerts"]:
            return False
        # Fraud customer identification: at least 2 of the 3 planted fraud customers
        identified = output.get("fraud_customer_ids", [])
        if not isinstance(identified, list):
            identified = []
        # Accept both IDs and names
        expected_ids = set(_t17_ev["fraud_customer_ids"])
        expected_names = set(n.lower() for n in _t17_ev["fraud_customer_names"])
        def _to_int(v: object) -> int | None:
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        matched = sum(
            1 for x in identified
            if (isinstance(x, int) and x in expected_ids)
            or (_to_int(x) is not None and _to_int(x) in expected_ids)
            or (isinstance(x, str) and x.lower() in expected_names)
        )
        if matched < 2:
            return False
        return True
    except (TypeError, ValueError, AttributeError):
        return False


T17 = TaskDefinition(
    task_id="T17",
    tier="complex",
    description=(
        "Fraud investigation: cross-reference transactions, customers, and "
        "alerts to identify fraud patterns and prioritize"
    ),
    task_prompt=(
        "You are investigating potential fraud. You have three data sources:\n"
        "1. Customers CSV at tests/evaluation/fixtures/t17_customers.csv "
        "(columns: customer_id, name, account_age_days, risk_score)\n"
        "2. Transactions CSV at tests/evaluation/fixtures/t17_transactions.csv "
        "(columns: txn_id, customer_id, amount, merchant, timestamp, flagged)\n"
        "3. Alerts JSON at tests/evaluation/fixtures/t17_alerts.json "
        "(list of alert objects with alert_id, txn_id, customer_id, reason, severity)\n\n"
        "Your investigation should:\n"
        "(1) Start by examining the alerts to understand the scope of potential fraud.\n"
        "(2) Cross-reference each alert with the transaction details (amount, merchant, timestamp) "
        "and customer profile (account age, risk score) to assess legitimacy.\n"
        "(3) Identify which customers are involved in genuine fraud by looking for patterns: "
        "multiple high-amount transactions, high-severity alerts, or unusually high risk scores.\n"
        "(4) Distinguish true fraud from false positive alerts (some alerts "
        "flag normal transactions).\n\n"
        "Adapt your investigation based on what you find — if a customer has multiple "
        "high-severity alerts, investigate their full transaction history. If an alert "
        "seems like a false positive (low amount, old account), deprioritize it.\n\n"
        "Return a dict with keys:\n"
        "- 'total_alerts' (int): total number of alerts in the dataset\n"
        "- 'high_severity_alerts' (int): count of alerts with severity='high'\n"
        "- 'fraud_customer_ids' (list): customer_ids (int) or names (str) of customers "
        "you identified as genuinely fraudulent (should be the top 3 most suspicious)"
    ),
    has_static_coverage=False,
    static_capability_name=None,
    fixture_paths=[
        FIXTURES_DIR / "t17_customers.csv",
        FIXTURES_DIR / "t17_transactions.csv",
        FIXTURES_DIR / "t17_alerts.json",
    ],
    validator=_validate_t17,
    output_contract=OutputContract(
        required_keys=["total_alerts", "high_severity_alerts", "fraud_customer_ids"],
    ),
)

# ---------------------------------------------------------------------------
# T18 — Record deduplication via hashing (MCP_TOOL)
# ---------------------------------------------------------------------------

_t18_ev = _EV["T18"]


def _validate_t18(output: dict) -> bool:
    """unique_count == 50 AND duplicate_count == 17 AND total_rows == 67."""
    try:
        ok_unique = int(output.get("unique_count", -1)) == _t18_ev["unique_count"]
        ok_dupes = int(output.get("duplicate_count", -1)) == _t18_ev["duplicate_count"]
        ok_total = int(output.get("total_rows", -1)) == _t18_ev["total_rows"]
        return ok_unique and ok_dupes and ok_total
    except (TypeError, ValueError):
        return False


T18 = TaskDefinition(
    task_id="T18",
    tier="moderate",
    description=(
        "Deduplicate records by hashing: compute hash per row, identify exact "
        "duplicates, return counts"
    ),
    task_prompt=(
        "Deduplicate the orders CSV at tests/evaluation/fixtures/t18_orders_with_dupes.csv. "
        "The file contains exact duplicate rows (same values in all columns). "
        "Compute a hash of each row (using all columns: order_id, customer, product, "
        "quantity, price), identify rows with identical hashes, and keep only unique rows.\n\n"
        "Return a dict with keys:\n"
        "- 'total_rows' (int): total rows in the input (excluding header)\n"
        "- 'unique_count' (int): number of unique rows after deduplication\n"
        "- 'duplicate_count' (int): number of duplicate rows removed"
    ),
    has_static_coverage=False,
    static_capability_name=None,
    fixture_paths=[FIXTURES_DIR / "t18_orders_with_dupes.csv"],
    validator=_validate_t18,
    output_contract=OutputContract(
        required_keys=["total_rows", "unique_count", "duplicate_count"],
    ),
)

# ---------------------------------------------------------------------------
# T19 — Exploratory data analysis (seeded AGENT, matches data-analysis-agent)
# ---------------------------------------------------------------------------

_t19_ev = _EV["T19"]


def _validate_t19(output: dict) -> bool:
    """n_rows correct AND n_categories correct AND categories match."""
    try:
        ok_rows = int(output.get("n_rows", -1)) == _t19_ev["n_rows"]
        ok_cats = int(output.get("n_categories", -1)) == _t19_ev["n_categories"]
        returned_cats = set(output.get("categories", []))
        ok_cat_names = returned_cats == set(_t19_ev["categories"])
        return ok_rows and ok_cats and ok_cat_names
    except (TypeError, ValueError):
        return False


T19 = TaskDefinition(
    task_id="T19",
    tier="moderate",
    description=(
        "Exploratory data analysis: profile product sales data, summarize by "
        "category, identify patterns"
    ),
    task_prompt=(
        "Perform exploratory data analysis on the product sales dataset at "
        "tests/evaluation/fixtures/t19_product_sales.csv "
        "(columns: product_id, category, revenue, units_sold, return_rate, satisfaction_score).\n\n"
        "Your analysis should:\n"
        "(1) Profile the dataset — row count, column count, data types.\n"
        "(2) Identify the distinct product categories.\n"
        "(3) Compute summary statistics (mean, min, max) for revenue across the full dataset.\n"
        "(4) Investigate which category has the highest total revenue and which has the "
        "highest average satisfaction score.\n\n"
        "This requires adaptive investigation — let the data guide which patterns "
        "you explore further.\n\n"
        "Return a dict with keys:\n"
        "- 'n_rows' (int): total rows in the dataset\n"
        "- 'n_categories' (int): number of distinct categories\n"
        "- 'categories' (list of str): the distinct category names"
    ),
    has_static_coverage=True,
    static_capability_name="data-analysis-agent",
    fixture_paths=[FIXTURES_DIR / "t19_product_sales.csv"],
    validator=_validate_t19,
    output_contract=OutputContract(
        required_keys=["n_rows", "n_categories", "categories"],
    ),
)

# ---------------------------------------------------------------------------
# T20 — Eligibility classification (pure SKILL, policy-driven)
# ---------------------------------------------------------------------------

_t20_ev = _EV["T20"]


def _validate_t20(output: dict) -> bool:
    """eligible/ineligible counts sum to total and rule count matches."""
    try:
        total = int(output.get("total_applications", -1))
        eligible = int(output.get("eligible_count", -1))
        ineligible = int(output.get("ineligible_count", -1))
        rules_applied = int(output.get("rules_applied", -1))
        ok_total = total == _t20_ev["total_applications"]
        ok_eligible = eligible == _t20_ev["eligible_count"]
        ok_ineligible = ineligible == _t20_ev["ineligible_count"]
        ok_rules = rules_applied == _t20_ev["rules_applied"]
        ok_sum = eligible + ineligible == total
        return ok_total and ok_eligible and ok_ineligible and ok_rules and ok_sum
    except (TypeError, ValueError):
        return False


T20 = TaskDefinition(
    task_id="T20",
    tier="moderate",
    description=(
        "Apply an eligibility policy (encoded as JSON rules) to classify "
        "business grant applications"
    ),
    task_prompt=(
        "Apply the eligibility policy to classify grant applications.\n\n"
        "The eligibility policy is encoded as a JSON rules file at "
        "tests/evaluation/fixtures/t20_eligibility_policy.json — it defines the program's "
        "eligibility criteria as a set of rules with operators (less_than, between, "
        "greater_than_or_equal, in_list) applied to specific application fields.\n\n"
        "The applications CSV at tests/evaluation/fixtures/t20_applications.csv contains "
        "the applicant data (columns: applicant_id, business_name, annual_revenue, "
        "employee_count, years_in_business, industry).\n\n"
        "For each application, apply ALL rules from the policy. An application is eligible "
        "only if it passes every rule. Count eligible vs ineligible applications.\n\n"
        "Return a dict with keys:\n"
        "- 'total_applications' (int): total number of applications\n"
        "- 'eligible_count' (int): number of applications passing all rules\n"
        "- 'ineligible_count' (int): number of applications failing at least one rule\n"
        "- 'rules_applied' (int): number of rules in the policy that were applied"
    ),
    has_static_coverage=True,
    static_capability_name="eligibility-policy-classifier",
    fixture_paths=[
        FIXTURES_DIR / "t20_applications.csv",
        FIXTURES_DIR / "t20_eligibility_policy.json",
    ],
    validator=_validate_t20,
    output_contract=OutputContract(
        required_keys=[
            "total_applications",
            "eligible_count",
            "ineligible_count",
            "rules_applied",
        ],
    ),
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Author-assigned ground-truth primary capability type per task for
# classification-accuracy analysis. Assignments are judgment calls based on
# each task's task_prompt and core-work shape:
#
# - MCP_TOOL: deterministic, stateless operation with a fixed input/output
#   contract (parse, validate, compute, transform, deduplicate).
# - SKILL: packaged procedural expertise driven by an external policy /
#   rules / templates (reorder policy from YAML, budget policy from JSON,
#   monitoring config, eligibility rules, multi-source workflow).
# - AGENT: adaptive reasoning where the investigation path depends on
#   intermediate findings (root-cause cascade diagnosis, fraud triage,
#   iterative anomaly investigation, exploratory profiling).
#
# None of these assignments are secret — the classifier in
# src/orchestrator/classifier.py sees the same task_prompt text and makes
# its own decision at pipeline time. The analyzer just compares.
_EXPECTED_TYPES: dict[str, str] = {
    "T01": "mcp_tool",   # Parse CSV → row count + column names (deterministic)
    "T02": "mcp_tool",   # Validate JSON against schema (deterministic)
    "T03": "mcp_tool",   # Word count + Flesch readability (deterministic)
    "T04": "skill",      # Inventory reorder policy from YAML rules (policy-driven)
    "T05": "mcp_tool",   # Clean CSV nulls + duplicates (deterministic)
    "T06": "mcp_tool",   # KNN imputation (algorithmic, deterministic)
    "T07": "mcp_tool",   # Fuzzy join + CSV output (algorithmic)
    "T08": "agent",      # Iterative data quality investigation (adaptive)
    "T09": "mcp_tool",   # Structured report generation (deterministic)
    "T10": "agent",      # Adaptive trend analysis: try, compare, select (adaptive)
    "T11": "skill",      # Multi-source data integration workflow (packaged)
    "T12": "skill",      # Data quality policy in JSON rules (policy-driven)
    "T13": "agent",      # Iterative anomaly investigation (adaptive)
    "T14": "skill",      # Budget allocation policy in JSON (policy-driven)
    "T15": "skill",      # Config-driven monitoring policy (policy-driven)
    "T16": "agent",      # Root cause diagnosis cascade (adaptive)
    "T17": "agent",      # Fraud investigation cross-referencing (adaptive)
    "T18": "mcp_tool",   # Deduplicate by hashing (deterministic)
    "T19": "agent",      # Exploratory data analysis (adaptive)
    "T20": "skill",      # Eligibility policy in JSON rules (policy-driven)
}

# Populate expected_type on each TaskDefinition. TaskDefinition is frozen
# (immutability is a long-standing invariant for the testbed — we don't
# want tasks mutating mid-eval), so this retrofit at import time uses
# object.__setattr__ to bypass the __setattr__ guard. Equivalent to
# inlining `expected_type=...` into each of the 20 T-task constructors,
# but centralizes the ground-truth mapping in _EXPECTED_TYPES above.
for _task in [
    T01, T02, T03, T04, T05, T06, T07, T08, T09, T10,
    T11, T12, T13, T14, T15, T16, T17, T18, T19, T20,
]:
    object.__setattr__(_task, "expected_type", _EXPECTED_TYPES[_task.task_id])

ALL_TASKS: list[TaskDefinition] = [
    T01, T02, T03, T04, T05, T06, T07, T08, T09, T10,
    T11, T12, T13, T14, T15, T16, T17, T18, T19, T20,
]

PILOT_TASKS: list[TaskDefinition] = [
    T01,  # seeded retrieval
    T07,  # moderate JIT fuzzy join
    T05,  # moderate JIT data cleaning with static coverage
]

_TASK_MAP: dict[str, TaskDefinition] = {t.task_id: t for t in ALL_TASKS}


def get_task(task_id: str) -> TaskDefinition:
    """Look up a task by ID. Raises KeyError on unknown ID."""
    return _TASK_MAP[task_id]
