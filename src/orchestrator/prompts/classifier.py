"""Prompt for the capability classifier (LLM-based, temperature=0)."""

CLASSIFICATION_PROMPT = """\
You are a capability classifier for a JIT synthesis pipeline. Your job is to
classify a capability gap into one of three types based on the **nature of the
action required**, not the domain vocabulary.

## Category Definitions

- **mcp_tool**: A function that takes an input and produces an output. It can
  be implemented as **a single Python async function**, even if that function
  internally performs multiple steps (read file, parse, compute, return).
  The key test: can this be one function with one return value?
  Examples: parsing a file format, computing a hash, converting units,
  validating a schema, formatting a string, joining two CSVs, analysing a
  log file, summarising data.

- **skill**: A multi-step procedural workflow that requires **orchestrating
  separate tools or stages with intermediate outputs passed between them**.
  The key distinction from mcp_tool is that no single function can do it —
  it requires a pipeline of distinct operations. Examples: ETL pipeline that
  ingests from 3 sources then loads into a warehouse, data cleaning workflow
  that coordinates deduplication + imputation + outlier-flagging across
  multiple datasets, report generation that queries multiple APIs and
  assembles sections.

- **agent**: An autonomous entity that requires **planning, reasoning, or
  adaptive behavior**. It makes decisions, adjusts its strategy based on
  intermediate results, and may operate over multiple turns. Examples:
  anomaly investigation, autonomous data exploration, adaptive monitoring.

## Gap Count Context

The task decomposition produced {total_gaps} total capability gap(s) for this
task. The gap count is contextual information, **not a routing rule**. Classify
based on what the gap description itself requires:

- If the description describes a single coherent computation (even with
  multiple internal steps like parse → compute → return), classify as
  **mcp_tool**.
- If the description mentions integrating **multiple distinct data sources**,
  coordinating **separate pipeline stages with intermediate outputs**, or
  applying **a sequence of configurable rules** driven by external
  configuration — classify as **skill**, even if there is only 1 gap.
- If the description requires **adaptive investigation**, **planning based on
  intermediate findings**, or **strategy adjustment** — classify as **agent**,
  even if there is only 1 gap.

A gap that says "parse a CSV and return row count" is mcp_tool regardless of
gap count. A gap that says "join sales data with product catalog, compute
regional revenues, then compare against targets" is skill — it orchestrates
distinct data sources through a pipeline, even if decomposition collapsed it
into one gap.

## Important Distinctions

The domain vocabulary (e.g., "JSON", "CSV", "data") does NOT determine the
type. What matters is whether it can be **one function** (mcp_tool) vs.
requires **orchestrating multiple separate tools** (skill) vs. requires
**autonomous planning** (agent):
- "Parse a log file and summarise statistics" → mcp_tool (one function)
- "Fuzzy-join two CSVs with normalisation" → mcp_tool (one function)
- "JSON data cleaning and normalization pipeline" → mcp_tool (one function)
- "ETL pipeline: ingest from 3 APIs, transform, load into warehouse" → skill
- "Join sales with products, compute revenues, compare against targets" → skill (multi-source integration with intermediate stages)
- "Apply configurable quality rules from JSON config to clean a dataset" → skill (externally configured multi-stage pipeline)
- "Investigate anomalies by profiling data and adapting strategy" → agent
- "Autonomous agent that investigates JSON schema violations" → agent

## Examples

Gap description: "Convert temperature between Celsius and Fahrenheit"
Search query: "convert temperature units"
Answer: classified_type=mcp_tool, confidence=0.95, rationale="Stateless unit \
conversion is a single atomic operation."

Gap description: "Parse a log file and return row count, timestamp validity, \
and null severity count"
Search query: "parse-log-file-and-summarize"
Answer: classified_type=mcp_tool, confidence=0.95, rationale="Parsing a file \
and computing summary statistics is implementable as one async function."

Gap description: "ETL pipeline: ingest CSV and JSON from 3 APIs, join and \
deduplicate, load into data warehouse"
Search query: "multi-source ETL pipeline"
Answer: classified_type=skill, confidence=0.9, rationale="Requires \
orchestrating multiple distinct data sources and a load step — cannot be one \
function."

Gap description: "Autonomous agent that investigates anomalies and adapts its \
search strategy"
Search query: "adaptive anomaly investigation agent"
Answer: classified_type=agent, confidence=0.95, rationale="Requires planning, \
adaptive reasoning, and strategy adjustment based on intermediate findings."

Gap description: "Join sales data with product catalog on product_id, compute \
per-region revenue totals, then compare against regional targets"
Search query: "integrate-sales-products-and-compare-to-targets"
Answer: classified_type=skill, confidence=0.9, rationale="Integrates three \
distinct data sources (sales CSV, product catalog JSON, targets CSV) through a \
multi-stage pipeline with intermediate join and aggregation steps."

Gap description: "Profile metrics data, detect anomalies by comparing against \
baselines, investigate clusters and correlations"
Search query: "profile-and-detect-anomalies-against-baselines"
Answer: classified_type=agent, confidence=0.85, rationale="Requires adaptive \
investigation: profiling data, detecting anomalies, then investigating whether \
they cluster or correlate — strategy adapts based on intermediate findings."

## Task

Gap description: "{description}"
Search query: "{query}"

Classify this gap. Respond with the type, your confidence (0-1), and a brief \
rationale explaining why this type fits based on the nature of the action.\
"""
