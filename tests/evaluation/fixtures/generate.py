"""Fixture generator for the JustInTimeMAS evaluation testbed.

Run once before evaluation begins to produce all fixture files and
pre-compute the expected_values.json that validators read at runtime.

Usage::

    python -m tests.evaluation.fixtures.generate
    # or
    from tests.evaluation.fixtures.generate import generate_all_fixtures
    generate_all_fixtures(Path("tests/evaluation/fixtures"))
"""

from __future__ import annotations

import csv
import io
import json
import math
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent


def generate_all_fixtures(
    fixtures_dir: Path = FIXTURES_DIR, seed: int = 42
) -> dict:
    """Generate all 17 task fixtures and return the expected_values dict.

    Idempotent — safe to call multiple times; files are overwritten.
    """
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    expected: dict = {}

    expected.update(_gen_t01(fixtures_dir, rng))
    expected.update(_gen_t02(fixtures_dir))
    expected.update(_gen_t03(fixtures_dir, rng))
    expected.update(_gen_t04(fixtures_dir, rng))
    expected.update(_gen_t05(fixtures_dir, rng))
    expected.update(_gen_t06(fixtures_dir, rng))
    expected.update(_gen_t07(fixtures_dir, rng))
    expected.update(_gen_t08(fixtures_dir, rng))
    expected.update(_gen_t09(fixtures_dir, rng))
    expected.update(_gen_t10(fixtures_dir, rng))
    expected.update(_gen_t11(fixtures_dir, rng))
    expected.update(_gen_t12(fixtures_dir, rng))
    expected.update(_gen_t13(fixtures_dir, rng))
    expected.update(_gen_t14(fixtures_dir, rng))
    expected.update(_gen_t15(fixtures_dir, rng))
    expected.update(_gen_t16(fixtures_dir, rng))
    expected.update(_gen_t17(fixtures_dir, rng))

    out_path = fixtures_dir / "expected_values.json"
    out_path.write_text(json.dumps(expected, indent=2))
    return expected


# ---------------------------------------------------------------------------
# T01 — Standard comma-delimited CSV
# ---------------------------------------------------------------------------

_T01_COLUMNS = ["id", "name", "age", "city"]
_CITIES = ["New York", "London", "Tokyo", "Paris", "Berlin", "Sydney", "Toronto"]
_NAMES = ["Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace", "Heidi"]


def _gen_t01(fixtures_dir: Path, rng: random.Random) -> dict:
    rows = 50
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_T01_COLUMNS)
    writer.writeheader()
    for i in range(1, rows + 1):
        writer.writerow(
            {
                "id": i,
                "name": rng.choice(_NAMES) + f"_{i}",
                "age": rng.randint(20, 65),
                "city": rng.choice(_CITIES),
            }
        )
    (fixtures_dir / "t01_standard.csv").write_text(buf.getvalue())
    return {
        "T01": {
            "row_count": rows,
            "column_names": _T01_COLUMNS,
        }
    }


# ---------------------------------------------------------------------------
# T02 — JSON config + schema validation
# ---------------------------------------------------------------------------

_T02_CONFIG = {
    "database": {
        "host": "localhost",
        "port": "not-a-number",  # intentional error: should be integer
        "name": "analytics_db",
    },
    "cache": {
        "ttl": -5,  # intentional error: must be >= 0
        "backend": "redis",
    },
    "logging": {
        "level": "VERBOSE",  # intentional error: not in enum
    },
}

_T02_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["database", "cache", "logging"],
    "properties": {
        "database": {
            "type": "object",
            "required": ["host", "port", "name"],
            "properties": {
                "host": {"type": "string"},
                "port": {"type": "integer"},
                "name": {"type": "string"},
            },
        },
        "cache": {
            "type": "object",
            "required": ["ttl", "backend"],
            "properties": {
                "ttl": {"type": "integer", "minimum": 0},
                "backend": {"type": "string"},
            },
        },
        "logging": {
            "type": "object",
            "required": ["level"],
            "properties": {
                "level": {
                    "type": "string",
                    "enum": ["DEBUG", "INFO", "WARNING", "ERROR"],
                }
            },
        },
    },
}

# Expected errors: keys match jsonschema validation error paths
_T02_EXPECTED_ERRORS = [
    "database.port: 'not-a-number' is not of type 'integer'",
    "cache.ttl: -5 is less than the minimum of 0",
    "logging.level: 'VERBOSE' is not one of ['DEBUG', 'INFO', 'WARNING', 'ERROR']",
]


def _gen_t02(fixtures_dir: Path) -> dict:
    (fixtures_dir / "t02_config.json").write_text(json.dumps(_T02_CONFIG, indent=2))
    (fixtures_dir / "t02_schema.json").write_text(json.dumps(_T02_SCHEMA, indent=2))
    return {
        "T02": {
            "expected_error_count": len(_T02_EXPECTED_ERRORS),
            "expected_errors": _T02_EXPECTED_ERRORS,
        }
    }


# ---------------------------------------------------------------------------
# T03 — Text document (word count + Flesch readability)
# ---------------------------------------------------------------------------

_T03_TEXT = """\
The Business Analytics Pipeline is a multi-stage data processing system \
designed to transform raw enterprise data into actionable insights. It \
ingests structured and semi-structured data from multiple upstream sources, \
applies configurable transformation rules, and produces standardized outputs \
suitable for downstream analytics and machine learning workflows.

Data quality is a central concern throughout the pipeline. Each ingestion \
stage applies schema validation, null-value detection, and duplicate \
elimination before forwarding records to the transformation layer. This \
ensures that downstream models operate on clean, consistent data rather \
than raw operational records that may contain noise or missing values.

The transformation layer supports a configurable set of operations including \
column renaming, type coercion, derived feature computation, and outlier \
flagging. Transformations are defined declaratively in YAML configuration \
files, making it straightforward to extend the pipeline without modifying \
core processing logic. All transformation steps are logged with timestamps \
and row-level provenance information for audit and reproducibility purposes.
"""


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _flesch_score(text: str) -> float:
    """Approximate Flesch Reading Ease score."""
    words = re.findall(r"\b\w+\b", text)
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    # Syllable approximation: vowel groups per word
    def syllables(word: str) -> int:
        count = len(re.findall(r"[aeiouAEIOU]+", word))
        return max(count, 1)

    n_words = len(words)
    n_sentences = max(len(sentences), 1)
    n_syllables = sum(syllables(w) for w in words)

    score = (
        206.835
        - 1.015 * (n_words / n_sentences)
        - 84.6 * (n_syllables / max(n_words, 1))
    )
    return round(max(0.0, min(100.0, score)), 2)


def _gen_t03(fixtures_dir: Path, _rng: random.Random) -> dict:
    (fixtures_dir / "t03_document.txt").write_text(_T03_TEXT)
    wc = _word_count(_T03_TEXT)
    rs = _flesch_score(_T03_TEXT)
    return {
        "T03": {
            "word_count": wc,
            "word_count_tolerance_pct": 2,
            "readability_score": rs,
            "readability_range": [0, 100],
        }
    }


# ---------------------------------------------------------------------------
# T04 — Inventory reorder pipeline (targets SKILL classification)
# ---------------------------------------------------------------------------

_T04_PRODUCTS = ["Widget-A", "Widget-B", "Gadget-X", "Gadget-Y", "Sensor-1",
                 "Sensor-2", "Module-P", "Module-Q", "Cable-S", "Cable-T"]


def _gen_t04(fixtures_dir: Path, rng: random.Random) -> dict:
    """Generate inventory CSV + supplier rules JSON for a reorder pipeline.

    The SKILL must: load inventory, apply reorder rules, identify items
    below threshold, compute reorder quantities, and summarize.
    """
    # --- Inventory CSV ---
    inventory_rows = []
    for i, product in enumerate(_T04_PRODUCTS):
        stock = rng.randint(5, 200)
        daily_usage = round(rng.uniform(1.0, 15.0), 1)
        unit_cost = round(rng.uniform(5.0, 100.0), 2)
        inventory_rows.append({
            "product_id": i + 1,
            "product_name": product,
            "current_stock": stock,
            "daily_usage": daily_usage,
            "unit_cost": unit_cost,
            "warehouse": rng.choice(["WH-North", "WH-South"]),
        })

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["product_id", "product_name",
                                              "current_stock", "daily_usage",
                                              "unit_cost", "warehouse"])
    writer.writeheader()
    writer.writerows(inventory_rows)
    (fixtures_dir / "t04_inventory.csv").write_text(buf.getvalue())

    # --- Reorder rules JSON ---
    min_days_stock = 14  # reorder if stock < 14 days of usage
    reorder_multiplier = 30  # order 30 days' worth
    rules = {
        "min_days_of_stock": min_days_stock,
        "reorder_days_supply": reorder_multiplier,
        "max_order_value": 5000.0,
    }
    (fixtures_dir / "t04_reorder_rules.json").write_text(json.dumps(rules, indent=2))

    # --- Compute expected values ---
    items_below_threshold = 0
    total_reorder_cost = 0.0
    for row in inventory_rows:
        days_of_stock = row["current_stock"] / row["daily_usage"] if row["daily_usage"] > 0 else 999
        if days_of_stock < min_days_stock:
            items_below_threshold += 1
            reorder_qty = math.ceil(row["daily_usage"] * reorder_multiplier)
            order_cost = reorder_qty * row["unit_cost"]
            total_reorder_cost += min(order_cost, rules["max_order_value"])

    return {
        "T04": {
            "product_count": len(_T04_PRODUCTS),
            "items_below_threshold": items_below_threshold,
            "total_reorder_cost": round(total_reorder_cost, 2),
            "min_days_of_stock": min_days_stock,
        }
    }


# ---------------------------------------------------------------------------
# T05 — Dirty CSV (nulls + duplicates)
# ---------------------------------------------------------------------------


def _gen_t05(fixtures_dir: Path, rng: random.Random) -> dict:
    columns = ["id", "customer", "revenue", "region"]
    clean_rows = []
    for i in range(1, 41):  # 40 unique clean rows
        clean_rows.append(
            {
                "id": i,
                "customer": rng.choice(_NAMES) + f"_co_{i}",
                "revenue": round(rng.uniform(1000.0, 50000.0), 2),
                "region": rng.choice(["North", "South", "East", "West"]),
            }
        )

    # Add 8 exact duplicates (rows 1-8 repeated)
    duplicate_rows = clean_rows[:8]

    # Add 6 rows with nulls
    null_rows = []
    for i in range(41, 47):
        null_rows.append(
            {
                "id": i,
                "customer": rng.choice(_NAMES) + f"_co_{i}",
                "revenue": "",  # null
                "region": rng.choice(["North", "South"]),
            }
        )

    all_rows = clean_rows + duplicate_rows + null_rows
    rng.shuffle(all_rows)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    writer.writerows(all_rows)
    (fixtures_dir / "t05_dirty.csv").write_text(buf.getvalue())

    return {
        "T05": {
            "input_row_count": len(all_rows),
            "expected_max_output_row_count": len(clean_rows),  # after dedup+null drop
        }
    }


# ---------------------------------------------------------------------------
# T06 — Dataset with ~15% missing values (KNN imputation)
# ---------------------------------------------------------------------------


def _gen_t06(fixtures_dir: Path, rng: random.Random) -> dict:
    columns = ["id", "feature_a", "feature_b", "feature_c", "feature_d"]
    n_rows = 100
    null_rate = 0.15

    rows = []
    for i in range(1, n_rows + 1):
        row: dict = {"id": i}
        for col in columns[1:]:
            val = round(rng.gauss(50.0, 10.0), 3)
            row[col] = "" if rng.random() < null_rate else val
        rows.append(row)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    (fixtures_dir / "t06_missing_values.csv").write_text(buf.getvalue())

    # Pre-compute expected post-KNN-imputation variance (population, ddof=0).
    #
    # We run a reference KNN imputation (k=5, Euclidean distance on shared
    # non-NaN features) and use the resulting full-dataset variance as the
    # ground truth.  This accounts for the inherent variance shrinkage of
    # KNN imputation (imputed values are neighbor means, which regress toward
    # the population mean).  Generated tools using sklearn.impute.KNNImputer
    # or equivalent manual implementations should produce variances within
    # ±10% of these reference values.
    feature_cols = columns[1:]
    n_cols = len(feature_cols)

    # Build a 2-D list (n_rows × n_cols), None for missing
    matrix: list[list[float | None]] = []
    for r in rows:
        matrix.append([float(r[c]) if r[c] != "" else None for c in feature_cols])

    # KNN imputation (k=5, Euclidean on shared non-None features)
    imputed = [list(row) for row in matrix]  # deep copy
    for i in range(n_rows):
        for j in range(n_cols):
            if matrix[i][j] is not None:
                continue
            # Collect distances to rows that have feature j
            dists: list[tuple[float, float]] = []
            for k in range(n_rows):
                if k == i or matrix[k][j] is None:
                    continue
                # Euclidean distance on shared non-None features (excl. j)
                shared_sq = 0.0
                n_shared = 0
                for f in range(n_cols):
                    if matrix[i][f] is not None and matrix[k][f] is not None:
                        shared_sq += (matrix[i][f] - matrix[k][f]) ** 2  # type: ignore[operator]
                        n_shared += 1
                if n_shared == 0:
                    continue
                dists.append((shared_sq ** 0.5, matrix[k][j]))  # type: ignore[arg-type]
            dists.sort(key=lambda x: x[0])
            neighbors = dists[:5]
            if neighbors:
                imputed[i][j] = sum(v for _, v in neighbors) / len(neighbors)

    # Compute population variance (ddof=0) of each column after imputation
    variances: dict[str, float] = {}
    for j, col in enumerate(feature_cols):
        vals = [imputed[i][j] for i in range(n_rows) if imputed[i][j] is not None]
        mean = sum(vals) / len(vals)
        variances[col] = round(sum((v - mean) ** 2 for v in vals) / len(vals), 4)

    return {
        "T06": {
            "n_rows": n_rows,
            "columns": feature_cols,
            "expected_variances": variances,
            "variance_tolerance_pct": 10,
        }
    }


# ---------------------------------------------------------------------------
# T07 — Fuzzy join on mismatched company names
# ---------------------------------------------------------------------------

_T07_BASE_COMPANIES = [
    "Acme Corp", "Globex", "Initech", "Umbrella", "Soylent",
    "Vandelay Industries", "Dunder Mifflin", "Bluth Company",
    "Prestige Worldwide", "Sterling Cooper",
    "Wonka Industries", "Gekko Associates", "Nakatomi Trading",
    "Oceanic Airlines", "Weyland Corp",
]

_T07_NOISE = [
    lambda s: s.upper(),
    lambda s: s.lower(),
    lambda s: "  " + s + "  ",
    lambda s: s.replace(" ", "  "),
    lambda s: s + " Ltd",
    lambda s: s + " Inc",
]


def _gen_t07(fixtures_dir: Path, rng: random.Random) -> dict:
    # Right dataset: clean canonical names
    right_rows = [
        {"company_id": i + 1, "company_name": name, "industry": rng.choice(["Tech", "Finance", "Retail"])}
        for i, name in enumerate(_T07_BASE_COMPANIES)
    ]

    # Left dataset: 15 of the 15 companies, with noise applied to names
    # Use only first 12 to guarantee 12 deterministic matches
    n_matches = 12
    matched_companies = _T07_BASE_COMPANIES[:n_matches]
    # 3 unmatched (novel companies not in right dataset)
    unmatched = ["FooBar LLC", "Zephyr Solutions", "Quantum Leap Co"]
    left_companies = matched_companies + unmatched
    rng.shuffle(left_companies)

    left_rows = []
    for i, name in enumerate(left_companies):
        if name in matched_companies:
            noise_fn = rng.choice(_T07_NOISE)
            noisy = noise_fn(name)
        else:
            noisy = name
        left_rows.append({"order_id": i + 1, "company_name": noisy, "amount": round(rng.uniform(100, 10000), 2)})

    buf_left = io.StringIO()
    writer = csv.DictWriter(buf_left, fieldnames=["order_id", "company_name", "amount"])
    writer.writeheader()
    writer.writerows(left_rows)

    buf_right = io.StringIO()
    writer = csv.DictWriter(buf_right, fieldnames=["company_id", "company_name", "industry"])
    writer.writeheader()
    writer.writerows(right_rows)

    (fixtures_dir / "t07_left.csv").write_text(buf_left.getvalue())
    (fixtures_dir / "t07_right.csv").write_text(buf_right.getvalue())

    return {
        "T07": {
            "left_row_count": len(left_rows),
            "right_row_count": len(right_rows),
            "expected_join_count": n_matches,  # only matched companies produce join rows
        }
    }


# ---------------------------------------------------------------------------
# T08 — Classification dataset (logistic regression)
# ---------------------------------------------------------------------------


def _gen_t08(fixtures_dir: Path, rng: random.Random) -> dict:
    """Generate a messy dataset for autonomous data quality investigation.

    The AGENT must: profile the data, identify quality issues (nulls,
    outliers, type mismatches), decide which fixes to apply, and report.
    """
    n_rows = 100
    columns = ["id", "name", "age", "salary", "department", "join_date"]
    rows = []

    departments = ["Engineering", "Sales", "Marketing", "Support", "HR"]

    for i in range(1, n_rows + 1):
        age = rng.randint(22, 65)
        salary = round(rng.gauss(60000, 15000), 2)
        dept = rng.choice(departments)
        base_date = datetime(2015, 1, 1) + timedelta(days=rng.randint(0, 3000))
        join_date = base_date.strftime("%Y-%m-%d")

        row = {
            "id": i,
            "name": rng.choice(_NAMES) + f" {chr(65 + rng.randint(0, 25))}.",
            "age": age,
            "salary": salary,
            "department": dept,
            "join_date": join_date,
        }

        # Inject quality issues
        # ~10% null ages
        if rng.random() < 0.10:
            row["age"] = ""
        # ~8% null salaries
        if rng.random() < 0.08:
            row["salary"] = ""
        # ~5% outlier salaries (negative or extremely high)
        if rng.random() < 0.05:
            row["salary"] = round(rng.choice([-5000, 500000, -1200, 999999]), 2)
        # ~5% invalid dates
        if rng.random() < 0.05:
            row["join_date"] = rng.choice(["not-a-date", "00/00/0000", ""])
        # ~3% wrong department names (typos)
        if rng.random() < 0.03:
            row["department"] = rng.choice(["Engneering", "Sals", "Mktg", ""])

        rows.append(row)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    (fixtures_dir / "t08_messy_data.csv").write_text(buf.getvalue())

    # Compute expected quality issue counts
    null_ages = sum(1 for r in rows if r["age"] == "")
    null_salaries = sum(1 for r in rows if r["salary"] == "")
    invalid_dates = sum(1 for r in rows if r["join_date"] in ("not-a-date", "00/00/0000", ""))
    salary_outliers = sum(
        1 for r in rows
        if r["salary"] != "" and (float(r["salary"]) < 0 or float(r["salary"]) > 300000)
    )
    total_issues = null_ages + null_salaries + invalid_dates + salary_outliers

    return {
        "T08": {
            "n_rows": n_rows,
            "n_columns": len(columns),
            "total_quality_issues": total_issues,
            "null_ages": null_ages,
            "null_salaries": null_salaries,
            "invalid_dates": invalid_dates,
            "salary_outliers": salary_outliers,
            "valid_departments": departments,
        }
    }


# ---------------------------------------------------------------------------
# T09 — Report generation data
# ---------------------------------------------------------------------------


def _gen_t09(fixtures_dir: Path, rng: random.Random) -> dict:
    """Structured dataset for a report generation task."""
    data = {
        "title": "Q4 2024 Sales Analysis",
        "period": {"start": "2024-10-01", "end": "2024-12-31"},
        "records": [
            {
                "region": region,
                "product": product,
                "revenue": round(rng.uniform(10000, 200000), 2),
                "units": rng.randint(50, 5000),
                "returns": rng.randint(0, 50),
            }
            for region in ["North", "South", "East", "West"]
            for product in ["Widget", "Gadget", "Doohickey"]
        ],
    }
    # Plant 5 clear anomalies (very high revenue outliers)
    for i in range(5):
        data["records"][i]["revenue"] = round(rng.uniform(500000, 1000000), 2)

    (fixtures_dir / "t09_report_data.json").write_text(json.dumps(data, indent=2))

    return {
        "T09": {
            "required_keys": ["summary", "stats_table", "anomalies"],
            "expected_anomaly_count": 5,
        }
    }


# ---------------------------------------------------------------------------
# T10 — Unstructured server log file
# ---------------------------------------------------------------------------

def _gen_t10(fixtures_dir: Path, rng: random.Random) -> dict:
    """Generate weekly sales data with trend + seasonality for forecasting.

    The AGENT must: analyze the time series, detect the upward trend,
    identify seasonal patterns, and forecast the next 4 weeks.
    """
    n_weeks = 52  # one year of weekly data
    base_date = datetime(2024, 1, 1)
    rows = []

    # Linear trend: ~1000 base + ~20/week growth
    # Seasonal: ~15% amplitude with period 13 weeks (quarterly)
    base_sales = 1000.0
    trend_per_week = 20.0
    seasonal_amplitude = 150.0
    seasonal_period = 13  # quarterly

    for i in range(n_weeks):
        date = base_date + timedelta(weeks=i)
        trend = base_sales + trend_per_week * i
        seasonal = seasonal_amplitude * math.sin(2 * math.pi * i / seasonal_period)
        noise = rng.gauss(0, 50)
        sales = round(max(0, trend + seasonal + noise), 2)
        rows.append({
            "week": date.strftime("%Y-%m-%d"),
            "sales": sales,
            "promotions": 1 if rng.random() < 0.15 else 0,
            "region": rng.choice(["North", "South", "East", "West"]),
        })

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["week", "sales", "promotions", "region"])
    writer.writeheader()
    writer.writerows(rows)
    (fixtures_dir / "t10_weekly_sales.csv").write_text(buf.getvalue())

    # Expected values: trend direction, approximate final value, data points
    last_4_sales = [r["sales"] for r in rows[-4:]]
    avg_last_4 = round(sum(last_4_sales) / len(last_4_sales), 2)
    first_4_sales = [r["sales"] for r in rows[:4]]
    avg_first_4 = round(sum(first_4_sales) / len(first_4_sales), 2)

    return {
        "T10": {
            "n_weeks": n_weeks,
            "trend_direction": "upward",  # agent should detect this
            "avg_first_4_weeks": avg_first_4,
            "avg_last_4_weeks": avg_last_4,
            "has_seasonality": True,
            "n_forecast_weeks": 4,
        }
    }


# ---------------------------------------------------------------------------
# T11 — Multi-source data integration (targets SKILL classification)
# ---------------------------------------------------------------------------

_T11_REGIONS = ["North", "South", "East", "West"]
_T11_CATEGORIES = ["Electronics", "Clothing", "Food"]


def _gen_t11(fixtures_dir: Path, rng: random.Random) -> dict:
    """Three data sources: sales CSV, products JSON, targets CSV."""
    # --- Products catalog (JSON) ---
    products = [
        {"product_id": i + 1, "name": f"Product_{chr(65 + i)}", "category": rng.choice(_T11_CATEGORIES)}
        for i in range(10)
    ]
    (fixtures_dir / "t11_products.json").write_text(json.dumps(products, indent=2))

    # --- Sales data (CSV) ---
    sales_rows = []
    for i in range(1, 61):
        pid = rng.choice(products)["product_id"]
        qty = rng.randint(1, 20)
        price = round(rng.uniform(10.0, 200.0), 2)
        region = rng.choice(_T11_REGIONS)
        sales_rows.append({
            "order_id": i,
            "product_id": pid,
            "quantity": qty,
            "unit_price": price,
            "region": region,
        })

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["order_id", "product_id", "quantity", "unit_price", "region"])
    writer.writeheader()
    writer.writerows(sales_rows)
    (fixtures_dir / "t11_sales.csv").write_text(buf.getvalue())

    # --- Pre-compute region revenues ---
    region_revenues: dict[str, float] = {}
    for row in sales_rows:
        rev = row["quantity"] * row["unit_price"]
        region_revenues[row["region"]] = round(
            region_revenues.get(row["region"], 0.0) + rev, 2
        )

    # --- Regional targets (CSV) ---
    target_rows = []
    for region in _T11_REGIONS:
        actual = region_revenues.get(region, 0.0)
        # Set target so ~2-3 regions meet it
        target = round(actual * rng.uniform(0.85, 1.15), 2)
        target_rows.append({"region": region, "target_revenue": target})

    buf_targets = io.StringIO()
    writer = csv.DictWriter(buf_targets, fieldnames=["region", "target_revenue"])
    writer.writeheader()
    writer.writerows(target_rows)
    (fixtures_dir / "t11_targets.csv").write_text(buf_targets.getvalue())

    # Compute regions meeting target
    targets_map = {r["region"]: r["target_revenue"] for r in target_rows}
    regions_meeting = sum(
        1 for region, rev in region_revenues.items()
        if rev >= targets_map.get(region, float("inf"))
    )

    return {
        "T11": {
            "summary_row_count": len(_T11_REGIONS),
            "region_revenues": region_revenues,
            "regions_meeting_target": regions_meeting,
            "revenue_tolerance_pct": 5,
        }
    }


# ---------------------------------------------------------------------------
# T12 — Configurable data quality pipeline (targets SKILL classification)
# ---------------------------------------------------------------------------


def _gen_t12(fixtures_dir: Path, rng: random.Random) -> dict:
    """Dirty CSV + rules JSON for a multi-step quality pipeline."""
    columns = ["id", "name", "age", "score", "department"]
    departments = ["Engineering", "Sales", "Marketing", "Support"]

    # Generate 60 clean rows
    clean_rows = []
    for i in range(1, 61):
        clean_rows.append({
            "id": i,
            "name": rng.choice(_NAMES) + f"_{i}",
            "age": rng.randint(22, 65),
            "score": round(rng.uniform(0.0, 100.0), 1),
            "department": rng.choice(departments),
        })

    # 8 rows with nulls in required columns
    null_rows = []
    for i in range(61, 69):
        row = {
            "id": i,
            "name": "",  # null name
            "age": rng.randint(22, 65),
            "score": round(rng.uniform(0.0, 100.0), 1),
            "department": rng.choice(departments),
        }
        null_rows.append(row)

    # 6 rows with out-of-range values (age < 18 or > 100)
    range_violation_rows = []
    for i in range(69, 75):
        row = {
            "id": i,
            "name": rng.choice(_NAMES) + f"_{i}",
            "age": rng.choice([rng.randint(1, 17), rng.randint(101, 150)]),
            "score": round(rng.uniform(0.0, 100.0), 1),
            "department": rng.choice(departments),
        }
        range_violation_rows.append(row)

    # 6 exact duplicates (rows 1-6 repeated)
    duplicate_rows = [dict(r) for r in clean_rows[:6]]

    all_rows = clean_rows + null_rows + range_violation_rows + duplicate_rows
    rng.shuffle(all_rows)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    writer.writerows(all_rows)
    (fixtures_dir / "t12_raw_data.csv").write_text(buf.getvalue())

    # Rules config
    rules = {
        "non_null_columns": ["name", "age", "score"],
        "range_constraints": {
            "age": {"min": 18, "max": 100},
            "score": {"min": 0.0, "max": 100.0},
        },
        "dedup_key_columns": ["id"],
    }
    (fixtures_dir / "t12_rules.json").write_text(json.dumps(rules, indent=2))

    # Expected: clean_rows survive all steps
    total_violations = len(null_rows) + len(range_violation_rows) + len(duplicate_rows)

    return {
        "T12": {
            "input_row_count": len(all_rows),
            "clean_row_count": len(clean_rows),
            "violations_found": total_violations,
            "rules_applied": 3,  # non-null, range, dedup
        }
    }


# ---------------------------------------------------------------------------
# T13 — Autonomous anomaly investigation (targets AGENT classification)
# ---------------------------------------------------------------------------

_T13_METRICS = ["cpu_usage", "memory_mb", "request_latency_ms", "error_rate"]


def _gen_t13(fixtures_dir: Path, rng: random.Random) -> dict:
    """Time-series metrics with planted anomalies + baselines JSON."""
    n_rows = 100
    base_time = datetime(2024, 6, 1, 0, 0, 0)

    # Normal ranges per metric
    baselines = {
        "cpu_usage": {"min": 10.0, "max": 80.0, "unit": "percent"},
        "memory_mb": {"min": 512.0, "max": 4096.0, "unit": "MB"},
        "request_latency_ms": {"min": 5.0, "max": 500.0, "unit": "ms"},
        "error_rate": {"min": 0.0, "max": 0.05, "unit": "ratio"},
    }

    rows = []
    anomaly_indices = set(rng.sample(range(n_rows), 5))
    planted_anomalies = []

    for i in range(n_rows):
        ts = base_time + timedelta(minutes=i * 15)
        row: dict = {"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S")}

        if i in anomaly_indices:
            # Pick a random metric to make anomalous
            anomaly_metric = rng.choice(_T13_METRICS)
            for metric in _T13_METRICS:
                bl = baselines[metric]
                if metric == anomaly_metric:
                    # Way outside normal range
                    val = round(bl["max"] * rng.uniform(2.5, 5.0), 3)
                    planted_anomalies.append({
                        "timestamp": row["timestamp"],
                        "metric": metric,
                        "value": val,
                    })
                else:
                    val = round(rng.uniform(bl["min"], bl["max"]), 3)
                row[metric] = val
        else:
            for metric in _T13_METRICS:
                bl = baselines[metric]
                row[metric] = round(rng.uniform(bl["min"], bl["max"]), 3)

        rows.append(row)

    # Write metrics CSV
    fieldnames = ["timestamp"] + _T13_METRICS
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    (fixtures_dir / "t13_metrics.csv").write_text(buf.getvalue())

    # Write baselines JSON
    (fixtures_dir / "t13_baselines.json").write_text(json.dumps(baselines, indent=2))

    return {
        "T13": {
            "anomaly_count": len(planted_anomalies),
            "planted_anomalies": planted_anomalies,
            "metrics_analyzed": len(_T13_METRICS),
        }
    }


# ---------------------------------------------------------------------------
# T14 — Budget reconciliation pipeline (targets SKILL classification)
# ---------------------------------------------------------------------------

_T14_DEPARTMENTS = ["Engineering", "Marketing", "Sales", "Support", "HR"]


def _gen_t14(fixtures_dir: Path, rng: random.Random) -> dict:
    """Three data sources: departments CSV, transactions CSV, allocation rules JSON."""
    # --- Departments with budgets ---
    dept_rows = [
        {"department": dept, "annual_budget": round(rng.uniform(100000, 500000), 2)}
        for dept in _T14_DEPARTMENTS
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["department", "annual_budget"])
    writer.writeheader()
    writer.writerows(dept_rows)
    (fixtures_dir / "t14_departments.csv").write_text(buf.getvalue())

    # --- Transactions (spending) ---
    txn_rows = []
    for i in range(1, 81):
        dept = rng.choice(_T14_DEPARTMENTS)
        txn_rows.append({
            "txn_id": i,
            "department": dept,
            "amount": round(rng.uniform(500, 25000), 2),
            "category": rng.choice(["salary", "equipment", "travel", "software", "training"]),
            "date": f"2024-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
        })
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["txn_id", "department", "amount", "category", "date"])
    writer.writeheader()
    writer.writerows(txn_rows)
    (fixtures_dir / "t14_transactions.csv").write_text(buf.getvalue())

    # --- Allocation rules ---
    rules = {
        "max_single_transaction": 20000,
        "category_caps": {
            "travel": 0.15,      # max 15% of budget
            "equipment": 0.25,   # max 25% of budget
        },
    }
    (fixtures_dir / "t14_rules.json").write_text(json.dumps(rules, indent=2))

    # Pre-compute expected values
    dept_spending: dict[str, float] = {}
    for txn in txn_rows:
        dept_spending[txn["department"]] = round(
            dept_spending.get(txn["department"], 0.0) + txn["amount"], 2
        )
    budgets = {d["department"]: d["annual_budget"] for d in dept_rows}
    over_budget = sum(
        1 for dept in _T14_DEPARTMENTS
        if dept_spending.get(dept, 0.0) > budgets[dept]
    )
    flagged_transactions = sum(
        1 for txn in txn_rows if txn["amount"] > rules["max_single_transaction"]
    )

    return {
        "T14": {
            "department_count": len(_T14_DEPARTMENTS),
            "department_spending": dept_spending,
            "spending_tolerance_pct": 5,
            "over_budget_count": over_budget,
            "flagged_transactions": flagged_transactions,
        }
    }


# ---------------------------------------------------------------------------
# T15 — Time-series ETL with configurable aggregation (targets SKILL)
# ---------------------------------------------------------------------------


def _gen_t15(fixtures_dir: Path, rng: random.Random) -> dict:
    """Hourly metrics CSV + aggregation config JSON."""
    # --- Hourly metrics (7 days x 24 hours = 168 rows) ---
    base_time = datetime(2024, 3, 1, 0, 0, 0)
    metrics_rows = []
    for i in range(168):
        ts = base_time + timedelta(hours=i)
        metrics_rows.append({
            "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "cpu_pct": round(rng.uniform(10, 90), 1),
            "memory_pct": round(rng.uniform(20, 85), 1),
            "requests_per_sec": rng.randint(50, 500),
            "error_count": rng.randint(0, 20),
        })

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["timestamp", "cpu_pct", "memory_pct", "requests_per_sec", "error_count"])
    writer.writeheader()
    writer.writerows(metrics_rows)
    (fixtures_dir / "t15_hourly_metrics.csv").write_text(buf.getvalue())

    # --- Aggregation config ---
    agg_config = {
        "aggregation_interval": "daily",
        "metrics": {
            "cpu_pct": ["mean", "max"],
            "memory_pct": ["mean", "max"],
            "requests_per_sec": ["sum"],
            "error_count": ["sum"],
        },
        "alert_thresholds": {
            "cpu_pct_max": 85,
            "memory_pct_max": 80,
            "error_count_sum": 100,
        },
    }
    (fixtures_dir / "t15_agg_config.json").write_text(json.dumps(agg_config, indent=2))

    # Pre-compute daily aggregates
    from collections import defaultdict
    daily_data: dict[str, list[dict]] = defaultdict(list)
    for row in metrics_rows:
        day = row["timestamp"][:10]
        daily_data[day].append(row)

    n_days = len(daily_data)
    alert_days = 0
    for day, rows in daily_data.items():
        cpu_max = max(float(r["cpu_pct"]) for r in rows)
        mem_max = max(float(r["memory_pct"]) for r in rows)
        error_sum = sum(int(r["error_count"]) for r in rows)
        if (cpu_max > agg_config["alert_thresholds"]["cpu_pct_max"]
                or mem_max > agg_config["alert_thresholds"]["memory_pct_max"]
                or error_sum > agg_config["alert_thresholds"]["error_count_sum"]):
            alert_days += 1

    return {
        "T15": {
            "n_days": n_days,
            "metrics_aggregated": len(agg_config["metrics"]),
            "alert_days": alert_days,
        }
    }


# ---------------------------------------------------------------------------
# T16 — Root cause diagnosis (targets AGENT classification)
# ---------------------------------------------------------------------------


def _gen_t16(fixtures_dir: Path, rng: random.Random) -> dict:
    """System metrics with a planted cascading failure chain."""
    n_rows = 120
    base_time = datetime(2024, 7, 1, 0, 0, 0)

    # Root cause: database connection pool exhaustion (db_connections spikes)
    # Cascade: db_connections↑ → query_latency↑ → request_queue↑ → error_rate↑
    root_cause_metric = "db_connections"
    cascade_chain = ["db_connections", "query_latency_ms", "request_queue_depth", "error_rate"]
    incident_start = 60  # row index where incident begins

    rows = []
    for i in range(n_rows):
        ts = base_time + timedelta(minutes=i * 5)
        row: dict = {"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S")}

        if i < incident_start:
            # Normal operation
            row["db_connections"] = rng.randint(5, 30)
            row["query_latency_ms"] = round(rng.uniform(5, 50), 1)
            row["request_queue_depth"] = rng.randint(0, 10)
            row["error_rate"] = round(rng.uniform(0.0, 0.02), 4)
            row["cpu_usage"] = round(rng.uniform(20, 60), 1)
        else:
            # Cascading failure with staggered onset
            elapsed = i - incident_start
            # Root cause: db_connections spike immediately
            row["db_connections"] = rng.randint(80, 100) if elapsed >= 0 else rng.randint(5, 30)
            # query_latency follows after ~5 steps
            row["query_latency_ms"] = round(rng.uniform(500, 2000), 1) if elapsed >= 5 else round(rng.uniform(5, 50), 1)
            # request_queue follows after ~10 steps
            row["request_queue_depth"] = rng.randint(50, 200) if elapsed >= 10 else rng.randint(0, 10)
            # error_rate follows after ~15 steps
            row["error_rate"] = round(rng.uniform(0.1, 0.5), 4) if elapsed >= 15 else round(rng.uniform(0.0, 0.02), 4)
            # cpu stays normal (red herring)
            row["cpu_usage"] = round(rng.uniform(20, 60), 1)

        rows.append(row)

    fieldnames = ["timestamp", "db_connections", "query_latency_ms", "request_queue_depth", "error_rate", "cpu_usage"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    (fixtures_dir / "t16_system_metrics.csv").write_text(buf.getvalue())

    # Incident description (what the agent sees)
    incident_report = {
        "title": "Service degradation detected",
        "symptoms": [
            "Elevated error rates observed",
            "Increased request queue depth",
            "User-reported slow response times",
        ],
        "onset_time": rows[incident_start]["timestamp"],
        "affected_service": "api-gateway",
    }
    (fixtures_dir / "t16_incident.json").write_text(json.dumps(incident_report, indent=2))

    return {
        "T16": {
            "root_cause_metric": root_cause_metric,
            "cascade_chain": cascade_chain,
            "incident_start_row": incident_start,
            "n_metrics": len(fieldnames) - 1,  # exclude timestamp
        }
    }


# ---------------------------------------------------------------------------
# T17 — Fraud investigation with cross-referencing (targets AGENT)
# ---------------------------------------------------------------------------

_T17_NAMES = ["Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace", "Heidi",
              "Ivan", "Judy", "Karl", "Linda", "Mike", "Nancy", "Oscar"]


def _gen_t17(fixtures_dir: Path, rng: random.Random) -> dict:
    """Transactions CSV + customers CSV + alerts JSON with planted fraud patterns."""
    # --- Customers ---
    customers = []
    for i, name in enumerate(_T17_NAMES):
        customers.append({
            "customer_id": i + 1,
            "name": name,
            "account_age_days": rng.randint(30, 2000),
            "risk_score": round(rng.uniform(0, 1), 2),
        })
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["customer_id", "name", "account_age_days", "risk_score"])
    writer.writeheader()
    writer.writerows(customers)
    (fixtures_dir / "t17_customers.csv").write_text(buf.getvalue())

    # --- Transactions (some fraudulent) ---
    fraud_customer_ids = [1, 3, 5]  # Alice, Carol, Eve
    transactions = []
    for i in range(1, 101):
        cid = rng.choice(range(1, len(_T17_NAMES) + 1))
        is_fraud = cid in fraud_customer_ids and rng.random() < 0.4
        amount = round(rng.uniform(5000, 50000) if is_fraud else rng.uniform(10, 2000), 2)
        transactions.append({
            "txn_id": i,
            "customer_id": cid,
            "amount": amount,
            "merchant": rng.choice(["ShopA", "ShopB", "ShopC", "OnlineX", "OnlineY"]),
            "timestamp": f"2024-06-{rng.randint(1,30):02d} {rng.randint(0,23):02d}:{rng.randint(0,59):02d}:00",
            "flagged": is_fraud,
        })
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["txn_id", "customer_id", "amount", "merchant", "timestamp", "flagged"])
    writer.writeheader()
    writer.writerows(transactions)
    (fixtures_dir / "t17_transactions.csv").write_text(buf.getvalue())

    # --- Alerts (pre-generated, some real, some false positives) ---
    alerts = []
    for txn in transactions:
        if txn["flagged"]:
            alerts.append({
                "alert_id": f"ALT-{txn['txn_id']:04d}",
                "txn_id": txn["txn_id"],
                "customer_id": txn["customer_id"],
                "reason": rng.choice(["high_amount", "velocity_spike", "unusual_merchant"]),
                "severity": "high",
            })
    # Add some false positive alerts (normal transactions flagged)
    normal_txns = [t for t in transactions if not t["flagged"]]
    for txn in rng.sample(normal_txns, min(5, len(normal_txns))):
        alerts.append({
            "alert_id": f"ALT-{txn['txn_id']:04d}",
            "txn_id": txn["txn_id"],
            "customer_id": txn["customer_id"],
            "reason": "amount_threshold",
            "severity": "low",
        })

    (fixtures_dir / "t17_alerts.json").write_text(json.dumps(alerts, indent=2))

    # Pre-compute expected values
    true_fraud_count = sum(1 for t in transactions if t["flagged"])
    fraud_customer_names = [c["name"] for c in customers if c["customer_id"] in fraud_customer_ids]
    total_alerts = len(alerts)
    high_severity_alerts = sum(1 for a in alerts if a["severity"] == "high")

    return {
        "T17": {
            "total_alerts": total_alerts,
            "high_severity_alerts": high_severity_alerts,
            "true_fraud_count": true_fraud_count,
            "fraud_customer_ids": fraud_customer_ids,
            "fraud_customer_names": fraud_customer_names,
        }
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    result = generate_all_fixtures()
    print(f"Generated fixtures in {FIXTURES_DIR}")
    print(f"Expected values written to {FIXTURES_DIR / 'expected_values.json'}")
    for task_id, vals in result.items():
        print(f"  {task_id}: {list(vals.keys())}")
