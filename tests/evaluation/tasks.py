"""Testbed task definitions for Iteration 5 evaluation.

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

Pilot tasks: T01 (seeded retrieval), T07 (moderate JIT), T05 (moderate JIT, static coverage).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

FIXTURES_DIR = Path(__file__).parent / "fixtures"
_EV_PATH = FIXTURES_DIR / "expected_values.json"

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
    is_pilot: bool = False


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
    description="Validate a JSON config against a JSON Schema and return the list of validation errors",
    task_prompt=(
        "Validate the JSON file at tests/evaluation/fixtures/t02_config.json "
        "against the schema at tests/evaluation/fixtures/t02_schema.json. "
        "Return a dict with key 'errors' (list of str describing each violation)."
    ),
    has_static_coverage=True,
    static_capability_name="json-validator",
    fixture_paths=[FIXTURES_DIR / "t02_config.json", FIXTURES_DIR / "t02_schema.json"],
    validator=_validate_t02,
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
    has_static_coverage=True,
    static_capability_name="text-statistics",
    fixture_paths=[FIXTURES_DIR / "t03_document.txt"],
    validator=_validate_t03,
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
    description="Inventory reorder pipeline: check stock levels against rules, compute reorder quantities",
    task_prompt=(
        "Build an inventory reorder pipeline using these two data sources:\n"
        "1. Inventory CSV at tests/evaluation/fixtures/t04_inventory.csv "
        "(columns: product_id, product_name, current_stock, daily_usage, unit_cost, warehouse)\n"
        "2. Reorder rules YAML at tests/evaluation/fixtures/t04_reorder_rules.yaml "
        "(contains min_days_of_stock threshold, reorder_days_supply multiplier, max_order_value cap)\n\n"
        "Pipeline stages:\n"
        "(1) Load both data sources.\n"
        "(2) For each product, compute days_of_stock = current_stock / daily_usage.\n"
        "(3) Identify products where days_of_stock < min_days_of_stock (these need reordering).\n"
        "(4) For each product needing reorder, compute reorder_qty = ceil(daily_usage * reorder_days_supply), "
        "then order_cost = reorder_qty * unit_cost, capped at max_order_value.\n"
        "(5) Sum total_reorder_cost across all products needing reorder.\n\n"
        "Return a dict with keys:\n"
        "- 'product_count' (int): total number of products\n"
        "- 'items_below_threshold' (int): number of products needing reorder\n"
        "- 'total_reorder_cost' (float): sum of capped order costs"
    ),
    has_static_coverage=False,
    static_capability_name=None,
    fixture_paths=[
        FIXTURES_DIR / "t04_inventory.csv",
        FIXTURES_DIR / "t04_reorder_rules.yaml",
    ],
    validator=_validate_t04,
)

# ---------------------------------------------------------------------------
# T05 — Data cleaning workflow
# ---------------------------------------------------------------------------

_t05_ev = _EV["T05"]


def _validate_t05(output: dict) -> bool:
    """output_row_count < input_row_count AND null_count == 0."""
    try:
        out_count = int(output.get("output_row_count", -1))
        null_count = int(output.get("null_count", -1))
        ok_reduced = out_count < _t05_ev["input_row_count"]
        ok_nulls = null_count == 0
        return ok_reduced and ok_nulls
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
    is_pilot=False,
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


T07 = TaskDefinition(
    task_id="T07",
    tier="moderate",
    description="Fuzzy join two datasets on company_name (whitespace and case differences)",
    task_prompt=(
        "Perform a fuzzy join between "
        "tests/evaluation/fixtures/t07_left.csv (orders) and "
        "tests/evaluation/fixtures/t07_right.csv (companies) "
        "on the 'company_name' column. Before matching: normalise case, strip whitespace, "
        "and strip common business suffixes (Inc, Ltd, Co, LLC). "
        "Return a dict with keys 'row_count' (int, matched join rows) "
        "and 'null_count_on_key' (int, null company_name values in result)."
    ),
    has_static_coverage=False,
    static_capability_name=None,
    fixture_paths=[FIXTURES_DIR / "t07_left.csv", FIXTURES_DIR / "t07_right.csv"],
    validator=_validate_t07,
    is_pilot=True,  # pilot task
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
    description="Data quality investigation: autonomously profile a messy dataset, identify and categorize issues",
    task_prompt=(
        "You are investigating data quality issues in a messy employee dataset.\n"
        "Dataset: tests/evaluation/fixtures/t08_messy_data.csv "
        "(columns: id, name, age, salary, department, join_date)\n\n"
        "Your task is to autonomously:\n"
        "(1) Load and profile the dataset — check shape, dtypes, null counts per column.\n"
        "(2) Identify quality issues: missing values (nulls/blanks), salary outliers "
        "(negative or unreasonably high values > $300,000), invalid dates, "
        "and department name typos/misspellings.\n"
        "(3) Categorize each issue type and count occurrences.\n"
        "(4) Decide which issues are most critical based on their frequency and impact.\n\n"
        "This requires adaptive investigation — the analysis path depends on what "
        "the initial profiling reveals. Examine the data before deciding what to check.\n\n"
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
)

# ---------------------------------------------------------------------------
# T09 — Report generation
# ---------------------------------------------------------------------------

_t09_ev = _EV["T09"]


def _validate_t09(output: dict) -> bool:
    """output has required keys AND anomalies list has exactly 5 items."""
    try:
        if not _check_keys_present(output, _t09_ev["required_keys"]):
            return False
        return len(output.get("anomalies", [])) == _t09_ev["expected_anomaly_count"]
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
)

# ---------------------------------------------------------------------------
# T10 — Trend detection and forecasting agent (targets AGENT)
# ---------------------------------------------------------------------------

_t10_ev = _EV["T10"]


def _validate_t10(output: dict) -> bool:
    """trend_direction == 'upward' AND n_weeks matches AND forecast provided."""
    try:
        ok_trend = output.get("trend_direction", "").lower() in ("upward", "up", "increasing", "positive")
        ok_weeks = int(output.get("n_weeks", -1)) == _t10_ev["n_weeks"]
        # Agent should provide some forecast (list of numbers or a summary)
        forecast = output.get("forecast") or output.get("forecast_values") or output.get("next_4_weeks")
        ok_forecast = forecast is not None and (isinstance(forecast, list) or isinstance(forecast, dict))
        return ok_trend and ok_weeks and ok_forecast
    except (TypeError, ValueError):
        return False


T10 = TaskDefinition(
    task_id="T10",
    tier="complex",
    description="Trend detection and forecasting: analyze weekly sales, detect trend and seasonality, forecast next 4 weeks",
    task_prompt=(
        "You are analyzing weekly sales data to detect trends and make forecasts.\n"
        "Dataset: tests/evaluation/fixtures/t10_weekly_sales.csv "
        "(columns: week, sales, promotions, region)\n\n"
        "Your task is to autonomously:\n"
        "(1) Load the data and examine the sales column over time.\n"
        "(2) Detect the overall trend direction (upward, downward, or flat) by "
        "comparing early weeks to later weeks.\n"
        "(3) Check for seasonality or cyclical patterns in the data.\n"
        "(4) Forecast the next 4 weeks of sales using an appropriate method "
        "(moving average, linear extrapolation, or any method you choose based "
        "on what the data shows).\n\n"
        "This requires adaptive analysis — examine the data first, then decide "
        "which forecasting approach is most appropriate based on the patterns you find.\n\n"
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
        ok_meeting = int(output.get("regions_meeting_target", -1)) == _t11_ev["regions_meeting_target"]
        return ok_meeting
    except (TypeError, ValueError, AttributeError):
        return False


T11 = TaskDefinition(
    task_id="T11",
    tier="complex",
    description="Multi-source data integration: join sales with products, compute regional revenue, compare against targets",
    task_prompt=(
        "Build a data integration pipeline using these three data sources:\n"
        "1. Sales CSV at tests/evaluation/fixtures/t11_sales.csv (columns: order_id, product_id, quantity, unit_price, region)\n"
        "2. Product catalog Excel at tests/evaluation/fixtures/t11_products.xlsx (columns: product_id, name, category)\n"
        "3. Regional targets CSV at tests/evaluation/fixtures/t11_targets.csv (columns: region, target_revenue)\n\n"
        "Steps: (1) Join the sales data with the product catalog on product_id to enrich sales records with product names and categories. "
        "(2) Compute per-region revenue totals by summing quantity * unit_price for each region. "
        "(3) Compare each region's total revenue against its target from the targets CSV.\n\n"
        "Return a dict with keys 'summary_row_count' (int, number of regions), "
        "'region_revenues' (dict mapping region name to float total revenue), "
        "and 'regions_meeting_target' (int, count of regions where revenue >= target)."
    ),
    has_static_coverage=False,
    static_capability_name=None,
    fixture_paths=[
        FIXTURES_DIR / "t11_sales.csv",
        FIXTURES_DIR / "t11_products.xlsx",
        FIXTURES_DIR / "t11_targets.csv",
    ],
    validator=_validate_t11,
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
    description="Configurable data quality pipeline: apply rules from JSON config to clean a dirty CSV",
    task_prompt=(
        "Build a data quality pipeline using these inputs:\n"
        "1. Raw data CSV at tests/evaluation/fixtures/t12_raw_data.csv (columns: id, name, age, score, department)\n"
        "2. Quality rules JSON at tests/evaluation/fixtures/t12_rules.json\n\n"
        "The rules config specifies three types of quality checks to apply in sequence:\n"
        "(1) Non-null enforcement: drop rows where any column listed in 'non_null_columns' is empty or null.\n"
        "(2) Range constraints: drop rows where values fall outside the min/max range specified in 'range_constraints'.\n"
        "(3) Deduplication: remove duplicate rows based on the columns listed in 'dedup_key_columns'.\n\n"
        "Apply these rules in the order listed above. "
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
)

# ---------------------------------------------------------------------------
# T13 — Autonomous anomaly investigation (targets AGENT)
# ---------------------------------------------------------------------------

_t13_ev = _EV["T13"]


def _validate_t13(output: dict) -> bool:
    """anomaly_count >= 3 (tolerant) AND anomalies list non-empty AND metrics_analyzed > 0."""
    try:
        anomaly_count = int(output.get("anomaly_count", 0))
        anomalies = output.get("anomalies", [])
        metrics_analyzed = int(output.get("metrics_analyzed", 0))
        return anomaly_count >= 3 and len(anomalies) > 0 and metrics_analyzed > 0
    except (TypeError, ValueError):
        return False


T13 = TaskDefinition(
    task_id="T13",
    tier="complex",
    description="Autonomous anomaly investigation: profile data, detect anomalies, cross-reference with baselines",
    task_prompt=(
        "Investigate anomalies in the metrics dataset. You have two data sources:\n"
        "1. Time-series metrics CSV at tests/evaluation/fixtures/t13_metrics.csv "
        "(columns: timestamp, cpu_usage, memory_mb, request_latency_ms, error_rate)\n"
        "2. Baselines JSON at tests/evaluation/fixtures/t13_baselines.json "
        "(normal ranges per metric with min/max values)\n\n"
        "Your investigation should:\n"
        "(1) Profile the data to understand the distribution of each metric.\n"
        "(2) Detect anomalies by comparing each metric's values against the baselines "
        "(values outside the normal min/max range are anomalous).\n"
        "(3) For each anomaly found, identify the timestamp, metric name, and anomalous value.\n\n"
        "Adapt your investigation based on what you find — if one metric has many anomalies, "
        "investigate whether they cluster in time or correlate with other metrics.\n\n"
        "Return a dict with keys 'anomaly_count' (int, total anomalies detected), "
        "'anomalies' (list of dicts, each with keys 'timestamp', 'metric', 'value'), "
        "and 'metrics_analyzed' (int, number of unique metrics you examined)."
    ),
    has_static_coverage=False,
    static_capability_name=None,
    fixture_paths=[
        FIXTURES_DIR / "t13_metrics.csv",
        FIXTURES_DIR / "t13_baselines.json",
    ],
    validator=_validate_t13,
)

# ---------------------------------------------------------------------------
# T14 — Budget reconciliation pipeline (targets SKILL)
# ---------------------------------------------------------------------------

_t14_ev = _EV["T14"]


def _validate_t14(output: dict) -> bool:
    """department_count correct AND spending within ±5% AND over_budget_count exact AND flagged_transactions exact."""
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
    description="Budget reconciliation pipeline: aggregate spending by department, compare against budgets, apply allocation rules",
    task_prompt=(
        "Build a budget reconciliation pipeline using these three data sources:\n"
        "1. Department budgets CSV at tests/evaluation/fixtures/t14_departments.csv "
        "(columns: department, annual_budget)\n"
        "2. Transactions TSV at tests/evaluation/fixtures/t14_transactions.tsv "
        "(tab-separated, columns: txn_id, department, amount, category, date)\n"
        "3. Allocation rules JSON at tests/evaluation/fixtures/t14_rules.json "
        "(contains max_single_transaction threshold and category_caps as fraction of budget)\n\n"
        "Pipeline stages:\n"
        "(1) Load all three data sources.\n"
        "(2) Aggregate total spending per department from the transactions.\n"
        "(3) Compare each department's total spending against its annual_budget to find over-budget departments.\n"
        "(4) Flag individual transactions that exceed the max_single_transaction threshold from the rules.\n\n"
        "Return a dict with keys:\n"
        "- 'department_count' (int): number of departments\n"
        "- 'department_spending' (dict): mapping department name to float total spending\n"
        "- 'over_budget_count' (int): number of departments where spending > annual_budget\n"
        "- 'flagged_transactions' (int): count of transactions exceeding max_single_transaction"
    ),
    has_static_coverage=False,
    static_capability_name=None,
    fixture_paths=[
        FIXTURES_DIR / "t14_departments.csv",
        FIXTURES_DIR / "t14_transactions.tsv",
        FIXTURES_DIR / "t14_rules.json",
    ],
    validator=_validate_t14,
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
    description="Time-series ETL pipeline: aggregate hourly metrics to daily per config, detect threshold breaches",
    task_prompt=(
        "Build a configurable time-series ETL pipeline using these inputs:\n"
        "1. Hourly metrics CSV at tests/evaluation/fixtures/t15_hourly_metrics.csv "
        "(columns: timestamp, cpu_pct, memory_pct, requests_per_sec, error_count)\n"
        "2. Aggregation config JSON at tests/evaluation/fixtures/t15_agg_config.json\n\n"
        "The config specifies:\n"
        "- 'aggregation_interval': 'daily' — group hourly rows by date\n"
        "- 'metrics': dict mapping each metric name to a list of aggregation functions "
        "(e.g., 'cpu_pct': ['mean', 'max'] means compute daily mean and daily max of cpu_pct)\n"
        "- 'alert_thresholds': dict mapping metric+function to threshold values "
        "(e.g., 'cpu_pct_max': 85 means alert if any day's max cpu_pct > 85)\n\n"
        "Pipeline stages:\n"
        "(1) Load the hourly metrics and parse timestamps.\n"
        "(2) Group rows by day (extract date from timestamp).\n"
        "(3) For each day, compute the aggregations specified in the config for each metric.\n"
        "(4) Check each day's aggregated values against the alert_thresholds and count alert days "
        "(a day is an alert day if ANY of its aggregated values exceeds the corresponding threshold).\n\n"
        "Return a dict with keys:\n"
        "- 'n_days' (int): number of unique days in the data\n"
        "- 'metrics_aggregated' (int): number of distinct metrics processed\n"
        "- 'alert_days' (int): number of days where at least one threshold was breached"
    ),
    has_static_coverage=False,
    static_capability_name=None,
    fixture_paths=[
        FIXTURES_DIR / "t15_hourly_metrics.csv",
        FIXTURES_DIR / "t15_agg_config.json",
    ],
    validator=_validate_t15,
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
    description="Root cause diagnosis: investigate a cascading system failure, identify the root cause metric and the cascade chain",
    task_prompt=(
        "You are investigating a service degradation incident. You have:\n"
        "1. System metrics CSV at tests/evaluation/fixtures/t16_system_metrics.csv "
        "(columns: timestamp, db_connections, query_latency_ms, request_queue_depth, error_rate, cpu_usage)\n"
        "2. Incident report YAML at tests/evaluation/fixtures/t16_incident.yaml "
        "(contains title, symptoms, onset_time, affected_service)\n\n"
        "The incident involves a cascading failure where one metric spiked first and "
        "caused other metrics to degrade in sequence. Your task is to:\n\n"
        "(1) Read the incident report to understand the symptoms and onset time.\n"
        "(2) Examine the metrics data around the onset time. Look at each metric's "
        "behavior before and after the incident start.\n"
        "(3) Identify which metric changed FIRST (the root cause) by comparing onset "
        "times of anomalies across metrics. The root cause metric is the one that "
        "deviated from normal earliest.\n"
        "(4) Determine the cascade chain — the order in which other metrics were affected "
        "after the root cause. Look for staggered onset patterns.\n"
        "(5) Note: not all metrics are part of the cascade. Some may remain normal "
        "throughout the incident (red herrings).\n\n"
        "This requires adaptive investigation: examine the data, form hypotheses about "
        "which metric is the root cause, test those hypotheses by checking temporal "
        "ordering, and revise if the evidence doesn't support your initial theory.\n\n"
        "Return a dict with keys:\n"
        "- 'root_cause_metric' (str): the name of the metric that spiked first\n"
        "- 'cascade_chain' (list of str): ordered list of metrics in the cascade "
        "(starting with the root cause, then each subsequent metric in order)\n"
        "- 'n_metrics_investigated' (int): how many distinct metrics you examined"
    ),
    has_static_coverage=False,
    static_capability_name=None,
    fixture_paths=[
        FIXTURES_DIR / "t16_system_metrics.csv",
        FIXTURES_DIR / "t16_incident.yaml",
    ],
    validator=_validate_t16,
)

# ---------------------------------------------------------------------------
# T17 — Fraud investigation with cross-referencing (targets AGENT)
# ---------------------------------------------------------------------------

_t17_ev = _EV["T17"]


def _validate_t17(output: dict) -> bool:
    """high_severity_alerts correct AND at least 2 of 3 fraud customers identified AND total_alerts correct."""
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
    description="Fraud investigation: cross-reference transactions, customers, and alerts to identify fraud patterns and prioritize",
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
        "(4) Distinguish true fraud from false positive alerts (some alerts flag normal transactions).\n\n"
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
    description="Deduplicate records by hashing: compute hash per row, identify exact duplicates, return counts",
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
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ALL_TASKS: list[TaskDefinition] = [T01, T02, T03, T04, T05, T06, T07, T08, T09, T10, T11, T12, T13, T14, T15, T16, T17, T18]

PILOT_TASKS: list[TaskDefinition] = [T01, T07, T05]  # retrieval + moderate JIT (fuzzy-join) + moderate JIT (data-cleaning, static coverage)

_TASK_MAP: dict[str, TaskDefinition] = {t.task_id: t for t in ALL_TASKS}


def get_task(task_id: str) -> TaskDefinition:
    """Look up a task by ID. Raises KeyError on unknown ID."""
    return _TASK_MAP[task_id]
