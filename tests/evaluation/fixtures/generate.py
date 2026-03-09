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
    """Generate all 10 task fixtures and return the expected_values dict.

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
# T04 — Pipe-delimited CSV with mixed types
# ---------------------------------------------------------------------------

_T04_COLUMNS = ["id", "product_name", "score", "active"]
_PRODUCTS = ["Widget", "Gadget", "Doohickey", "Thingamajig", "Whatsit"]


def _gen_t04(fixtures_dir: Path, rng: random.Random) -> dict:
    rows = 30
    lines = ["|".join(_T04_COLUMNS)]
    for i in range(1, rows + 1):
        lines.append(
            "|".join(
                [
                    str(i),
                    rng.choice(_PRODUCTS) + f"_{i}",
                    str(round(rng.uniform(0.0, 10.0), 2)),
                    str(rng.choice([True, False])).lower(),
                ]
            )
        )
    (fixtures_dir / "t04_pipe_delimited.csv").write_text("\n".join(lines))
    return {
        "T04": {
            "row_count": rows,
            "delimiter": "|",
            "column_names": _T04_COLUMNS,
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

    # Pre-compute variance of each numeric column (non-null values only)
    variances: dict[str, float] = {}
    for col in columns[1:]:
        vals = [
            float(r[col]) for r in rows if r[col] != ""
        ]
        mean = sum(vals) / len(vals)
        variances[col] = round(sum((v - mean) ** 2 for v in vals) / len(vals), 4)

    return {
        "T06": {
            "n_rows": n_rows,
            "columns": columns[1:],
            "original_variances": variances,
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
    """Create a dataset where LR can beat majority baseline by >5%."""
    n_rows = 200
    # 30% positive class → majority baseline accuracy = 0.70
    n_positive = 60
    n_negative = 140

    rows = []
    # Positive class: feature_x high, feature_y low
    for _ in range(n_positive):
        rows.append({
            "feature_x": round(rng.gauss(8.0, 1.5), 3),
            "feature_y": round(rng.gauss(2.0, 1.0), 3),
            "feature_z": round(rng.gauss(5.0, 2.0), 3),
            "label": 1,
        })
    # Negative class: feature_x low, feature_y high
    for _ in range(n_negative):
        rows.append({
            "feature_x": round(rng.gauss(2.0, 1.5), 3),
            "feature_y": round(rng.gauss(8.0, 1.0), 3),
            "feature_z": round(rng.gauss(5.0, 2.0), 3),
            "label": 0,
        })

    rng.shuffle(rows)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["feature_x", "feature_y", "feature_z", "label"])
    writer.writeheader()
    writer.writerows(rows)
    (fixtures_dir / "t08_classification.csv").write_text(buf.getvalue())

    majority_class_accuracy = n_negative / n_rows  # 0.70

    return {
        "T08": {
            "n_rows": n_rows,
            "majority_class_accuracy": majority_class_accuracy,
            "accuracy_margin": 0.05,
            "features": ["feature_x", "feature_y", "feature_z"],
            "label_column": "label",
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

_SEVERITIES = ["INFO", "WARNING", "ERROR", "DEBUG"]
_LOG_MESSAGES = [
    "User {user} logged in",
    "Request processed in {ms}ms",
    "Database query returned {n} rows",
    "Cache miss for key {key}",
    "Connection timeout to {host}",
    "Authentication failed for {user}",
    "Disk usage at {pct}%",
    "Scheduled job {job} completed",
]


def _gen_t10(fixtures_dir: Path, rng: random.Random) -> dict:
    n_lines = 200
    base_time = datetime(2024, 1, 15, 8, 0, 0)
    lines = []
    for i in range(n_lines):
        ts = base_time + timedelta(seconds=i * rng.randint(1, 30))
        severity = rng.choice(_SEVERITIES)
        msg_template = rng.choice(_LOG_MESSAGES)
        msg = msg_template.format(
            user=rng.choice(_NAMES),
            ms=rng.randint(10, 5000),
            n=rng.randint(0, 10000),
            key=f"cache:{rng.randint(1, 999)}",
            host=f"db-{rng.randint(1, 5)}.internal",
            pct=rng.randint(50, 99),
            job=f"job_{rng.randint(1, 20)}",
        )
        lines.append(f"{ts.strftime('%Y-%m-%d %H:%M:%S')} {severity} {msg}")

    (fixtures_dir / "t10_server.log").write_text("\n".join(lines))

    return {
        "T10": {
            "expected_log_lines": n_lines,
            "columns": ["timestamp", "severity", "message"],
            "valid_severities": _SEVERITIES,
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
