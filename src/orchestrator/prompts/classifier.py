"""Prompt for the capability classifier (LLM-based, temperature=0)."""

CLASSIFICATION_PROMPT = """\
You are a capability classifier for a JIT synthesis pipeline. Your job is to
classify a capability gap into one of three types based on the **nature of the
action required**, not the domain vocabulary.

## Category Definitions

- **mcp_tool**: A stateless, deterministic function that takes an input and
  produces an output with no side effects. It performs a single atomic
  operation. Examples: parsing a file format, computing a hash, converting
  units, validating a schema, formatting a string.

- **skill**: A multi-step procedural workflow that coordinates several
  operations in sequence. It may involve data transformation pipelines,
  domain-specific processes, or structured methodologies. The key distinction
  from mcp_tool is that it requires **multiple steps orchestrated together**.
  Examples: ETL pipeline, data cleaning workflow, report generation process.

- **agent**: An autonomous entity that requires **planning, reasoning, or
  adaptive behavior**. It makes decisions, adjusts its strategy based on
  intermediate results, and may operate over multiple turns. Examples:
  anomaly investigation, autonomous data exploration, adaptive monitoring.

## Important Distinctions

The domain vocabulary (e.g., "JSON", "CSV", "data") does NOT determine the
type. What matters is the **complexity and nature of the action**:
- "Parse a JSON file" → mcp_tool (single atomic operation)
- "JSON data cleaning and normalization pipeline" → skill (multi-step workflow)
- "Autonomous agent that investigates JSON schema violations" → agent (planning + reasoning)

## Examples

Gap description: "Convert temperature between Celsius and Fahrenheit"
Search query: "convert temperature units"
Answer: classified_type=mcp_tool, confidence=0.95, rationale="Stateless unit \
conversion is a single atomic operation with no side effects."

Gap description: "Multi-step data cleaning pipeline for financial records"
Search query: "financial data cleaning pipeline"
Answer: classified_type=skill, confidence=0.9, rationale="A multi-step \
procedural workflow that coordinates cleaning, validation, and normalization."

Gap description: "Autonomous agent that investigates anomalies and adapts its \
search strategy"
Search query: "adaptive anomaly investigation agent"
Answer: classified_type=agent, confidence=0.95, rationale="Requires planning, \
adaptive reasoning, and strategy adjustment based on intermediate findings."

## Task

Gap description: "{description}"
Search query: "{query}"

Classify this gap. Respond with the type, your confidence (0-1), and a brief \
rationale explaining why this type fits based on the nature of the action.\
"""
