"""Prompt for the capability classifier (LLM-based, temperature=0).

Classification is based on three architectural layers (industry consensus):
- MCP Tool = execution layer (atomic action, stateless, deterministic)
- Skill = knowledge layer (procedural expertise, prescribed workflow)
- Agent = reasoning layer (autonomous, adaptive, unpredictable path)

References: MCP spec (modelcontextprotocol.io), Agent Skills spec
(agentskills.io), Anthropic "Building Effective Agents", LangGraph docs.
"""

CLASSIFICATION_PROMPT = """\
You are a capability classifier for a JIT synthesis pipeline. Your job is to \
classify a capability gap into one of three architectural layers based on the \
nature of the capability required.

## The Three Layers

These are complementary architectural layers, not points on a complexity axis. \
Each solves a fundamentally different problem.

### mcp_tool — The Execution Layer
An MCP tool is a **single, stateless, well-defined operation** with clear \
inputs and outputs. It performs an action and returns a result. Think of it as \
a kitchen utensil or ingredient — it does one thing reliably.

The key test: **can the output be computed from the input in a single pass?** \
Even if that pass internally reads files, joins data, computes statistics, and \
returns a result — if the procedure is deterministic and self-contained in one \
function, it is a tool.

Examples: parsing a file, computing a hash, converting units, validating a \
schema, joining two CSVs, summarising data, deduplicating records via hashing.

### skill — The Knowledge Layer
A skill is **packaged procedural knowledge** that teaches an agent how to \
approach a domain-specific task. It encodes a prescribed, multi-step workflow \
with domain expertise, validation steps, and guardrails. Think of it as a \
recipe card — it does not execute anything itself, but an agent following it \
gains specialist expertise.

The key test: **is there a known, prescribable procedure where domain \
expertise matters?** The task has a defined sequence of steps, but those steps \
require domain knowledge about what to do, what order, what to validate, and \
what guardrails to apply. A skill captures the "how to approach this class of \
problems" knowledge that a tool cannot.

Examples: inventory reorder pipeline (check stock, apply reorder rules, \
compute costs), data quality pipeline (apply configurable rules to clean a \
dataset), multi-source report generation (join sources, aggregate, compare \
against targets), ETL workflows with validation stages.

### agent — The Reasoning Layer
An agent is an **autonomous entity that dynamically directs its own process**. \
It makes decisions, adjusts strategy based on intermediate findings, and \
pursues open-ended goals where the path to completion is unpredictable. Think \
of it as the cook — it decides what to do next based on what it observes.

The key test: **is the path to completion unpredictable, requiring adaptive \
reasoning?** The task cannot be reduced to a prescribed sequence of steps \
because intermediate results change what should happen next. The agent must \
plan, explore, and adjust its strategy.

Examples: anomaly investigation (profile data, then decide what to \
investigate based on what anomalies appear), root cause diagnosis (trace \
cascading failures across systems, adapting strategy as evidence accumulates), \
fraud detection (cross-reference patterns, follow leads autonomously).

## Decision Heuristics (apply in order)

1. **If the operation is stateless and well-defined → mcp_tool.** Can you \
specify the inputs and outputs completely? Is the procedure deterministic? \
Even complex computations (fuzzy joins, statistical analysis, file parsing) \
are tools if they are self-contained.

2. **If the task requires a prescribed multi-step procedure with domain \
expertise → skill.** Is there a known workflow with validation steps and \
guardrails? Would an expert write a standard operating procedure for this? \
The domain knowledge of "how to approach this" is the value, not just the \
individual steps.

3. **If the goal is open-ended and the path depends on intermediate findings \
→ agent.** Must the system explore, adapt, and make dynamic decisions? Can \
you NOT predict the steps in advance because they depend on what is \
discovered along the way?

## Gap Count Context

The task decomposition produced {total_gaps} total capability gap(s) for this \
task. The gap count is contextual information, **not a routing rule**. \
Classify based on what the gap description itself requires.

## Important Distinctions

The domain vocabulary (e.g., "JSON", "CSV", "data", "pipeline") does NOT \
determine the type. What matters is the nature of the capability:

- "Parse a log file and summarise statistics" → mcp_tool (deterministic, \
self-contained computation)
- "Fuzzy-join two CSVs with normalisation" → mcp_tool (single-pass operation)
- "Deduplicate records via hashing" → mcp_tool (stateless transformation)
- "Inventory reorder analysis: check stock levels against thresholds, apply \
reorder rules, compute costs" → skill (prescribed domain workflow with rules)
- "Join sales with products, compute revenues, compare against targets" → \
skill (multi-source integration following a known procedure)
- "Apply configurable quality rules from JSON config to clean a dataset" → \
skill (externally configured workflow with domain guardrails)
- "Investigate anomalies by profiling data and adapting strategy" → agent \
(open-ended, strategy adapts based on findings)
- "Diagnose root cause across cascading metric failures" → agent (must trace \
and adapt based on what is discovered)

## Few-Shot Examples

Gap description: "Convert temperature between Celsius and Fahrenheit"
Search query: "convert temperature units"
Answer: classified_type=mcp_tool, confidence=0.95, rationale="Stateless unit \
conversion is a single atomic operation with clear input and output."

Gap description: "Parse a log file and return row count, timestamp validity, \
and null severity count"
Search query: "parse-log-file-and-summarize"
Answer: classified_type=mcp_tool, confidence=0.95, rationale="Parsing a file \
and computing summary statistics is a deterministic, self-contained operation."

Gap description: "Inventory reorder analysis: identify products below reorder \
threshold, compute reorder quantities and costs"
Search query: "inventory-reorder-pipeline"
Answer: classified_type=skill, confidence=0.9, rationale="Requires domain \
knowledge about inventory management — checking stock against thresholds, \
applying reorder rules, computing costs. This is a prescribed workflow with \
domain-specific guardrails, not a single computation."

Gap description: "ETL pipeline: ingest CSV and JSON from 3 sources, join and \
deduplicate, load into data warehouse"
Search query: "multi-source ETL pipeline"
Answer: classified_type=skill, confidence=0.9, rationale="Multi-source data \
integration following a known ETL procedure with validation between stages. \
The domain knowledge of how to join, deduplicate, and load is the value."

Gap description: "Autonomous agent that investigates anomalies and adapts its \
search strategy"
Search query: "adaptive anomaly investigation agent"
Answer: classified_type=agent, confidence=0.95, rationale="Requires adaptive \
reasoning — the investigation path depends on what anomalies are found. \
Cannot be prescribed in advance."

Gap description: "Join sales data with product catalog on product_id, compute \
per-region revenue totals, then compare against regional targets"
Search query: "integrate-sales-products-and-compare-to-targets"
Answer: classified_type=skill, confidence=0.9, rationale="Multi-source \
integration following a prescribed procedure: join, aggregate, compare. The \
domain knowledge of how to combine these sources is the value."

Gap description: "Profile metrics data, detect anomalies by comparing against \
baselines, investigate clusters and correlations"
Search query: "profile-and-detect-anomalies-against-baselines"
Answer: classified_type=agent, confidence=0.85, rationale="Open-ended \
investigation: the system must profile, detect, then adaptively investigate \
whether anomalies cluster or correlate — strategy depends on intermediate \
findings."

## Task Context

Original task description (for domain context — classify the **gap**, not the \
whole task): {task_context}

## Gap to Classify

Gap description: "{description}"
Search query: "{query}"

Classify this gap. Respond with the type, your confidence (0-1), and a brief \
rationale explaining why this type fits based on the architectural layer.\
"""
