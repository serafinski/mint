"""Prompt for the task-level capability classifier (LLM-based, temperature=0).

Classification is based on three architectural layers (industry consensus):
- MCP Tool = execution layer (atomic action, stateless, deterministic)
- Skill = knowledge layer (procedural expertise, prescribed workflow)
- Agent = reasoning layer (autonomous, adaptive, unpredictable path)

The classifier decides the PRIMARY capability type for an entire task,
not for individual decomposition steps. This matches the testbed's
single-type-per-task ground truth labels.

References: MCP spec (modelcontextprotocol.io), Agent Skills spec
(agentskills.io), Anthropic "Building Effective Agents", LangGraph docs.
"""

TASK_CLASSIFICATION_PROMPT = """\
You are a capability classifier for a JIT synthesis pipeline. Your job is to \
classify an entire task into ONE architectural layer based on what kind of \
capability best solves the task end-to-end.

## The Three Layers

These are complementary architectural layers, not points on a complexity axis. \
Each solves a fundamentally different problem.

### mcp_tool — The Execution Layer
An MCP tool is a **single, stateless, well-defined operation** with clear \
inputs and outputs. It performs an action and returns a result.

The key test: **can the entire task be solved by a single deterministic \
function?** Even if that function internally reads files, joins data, and \
computes statistics — if the procedure is deterministic and self-contained, \
the task is an MCP tool.

Examples: parsing a CSV, computing a hash, validating a schema, joining two \
datasets, deduplicating records via hashing, fuzzy-matching strings.

### skill — The Knowledge Layer
A skill is **packaged procedural knowledge** that teaches an agent how to \
approach a domain-specific task. It encodes a prescribed, multi-step workflow \
with domain expertise, validation steps, and guardrails.

The key test: **does the task require a prescribed workflow with domain \
expertise where the steps are known but non-trivial?** If an expert would \
write a standard operating procedure for the task — and that procedure \
involves applying domain rules, validating intermediate results, or \
integrating multiple data sources — it is a skill.

Examples: inventory reorder pipeline (check stock, apply reorder rules, \
compute costs with caps), configurable data quality pipeline (apply rules \
from config to clean a dataset), multi-source revenue integration (join \
sources, aggregate, compare against targets).

### agent — The Reasoning Layer
An agent is an **autonomous entity that dynamically directs its own process**. \
It makes decisions, adjusts strategy based on intermediate findings, and \
pursues open-ended goals where the path to completion is unpredictable.

The key test: **is the path to completion unpredictable, requiring adaptive \
reasoning?** The task cannot be reduced to a prescribed sequence of steps \
because intermediate results change what should happen next.

Examples: root cause diagnosis (trace cascading failures, adapt strategy as \
evidence accumulates), anomaly investigation (profile data, decide what to \
investigate based on what anomalies appear), fraud detection (cross-reference \
patterns, follow leads).

## Decision Heuristics (apply in order)

1. **Is the task a single deterministic computation?** → mcp_tool
   Even complex computations (statistical analysis, fuzzy joins, multi-file \
   parsing) are mcp_tools if the full procedure can be specified in advance \
   and implemented as one function.

2. **Is there a known, prescribable workflow with domain rules?** → skill
   If the task has a defined sequence of steps with validation rules, \
   thresholds, or configurable parameters, and the "how to approach it" \
   knowledge is domain-specific — it is a skill.

3. **Is the goal open-ended with an unpredictable path?** → agent
   If the task requires exploration, hypothesis formation, or strategy \
   adjustment based on intermediate findings — it is an agent.

## Important Distinctions

The domain vocabulary (e.g., "JSON", "CSV", "data", "pipeline") does NOT \
determine the type. What matters is the nature of the task:

- Multi-step ≠ skill. A task with multiple internal steps can still be an \
  mcp_tool if those steps are deterministic and self-contained.
- Complex ≠ agent. A complex task with a known workflow is a skill, not an \
  agent. Agents are reserved for genuinely adaptive reasoning.
- "Workflow" language ≠ skill. Many tasks describe a workflow but can be \
  implemented as a single function.

## Decomposition Context

The task decomposition produced these required capabilities as guidance:
{required_capabilities}

This is contextual information about what the decomposer thought was needed. \
It is **not a routing rule**. Classify the task based on what single \
capability type best solves it end-to-end, even if the decomposer suggested \
multiple sub-steps (sub-steps are implementation details of one capability).

## Capability Naming

You must also propose a short, semantic `capability_name` for the generated \
capability. This name will be used as the registry identifier and should \
describe WHAT THE CAPABILITY DOES, not how the task was phrased.

### Naming rules
- **Kebab-case** (lowercase letters, digits, and hyphens only)
- **3-5 words**, maximum 40 characters
- **Describe the capability, not the task prompt**. A good name is reusable \
across semantically similar tasks.
- **Avoid task-specific verbs** like "parse-this", "build-a", \
"you-are-investigating", "read-the". These come from the task prompt \
phrasing and make the name look like a fragment of one specific task.
- **Avoid domain-flavored specificity** like "t16-metrics" or "inventory-csv-1". \
Use general concepts the capability could be reused for.
- **Prefer reusable nouns over specific verbs**: `xml-revenue-extractor` \
(reusable) vs `parse-this-xml-report-data` (one-shot).

### Good vs bad names

| Task | BAD name (task-prefix) | GOOD name (reusable) |
|---|---|---|
| "Parse this CSV file and return row count and column names" | `parse-this-csv-file-and-return-row-count` | `csv-row-column-summarizer` |
| "Build an inventory reorder pipeline using inventory CSV and..." | `build-an-inventory-reorder-pipeline-using` | `inventory-reorder-calculator` |
| "You are investigating a service degradation incident..." | `you-are-investigating-a-service-degradat` | `cascade-root-cause-investigator` |
| "Parse this XML report data and find the top 5 revenue anomalies" | `parse-this-xml-report-data-and-find-the` | `xml-revenue-top-k-extractor` |
| "Read the departments CSV and the transactions TSV. Aggregate..." | `read-the-departments-csv-and-the-transac` | `department-spend-aggregator` |
| "Clean a dirty CSV by removing duplicates and nulls" | `clean-a-dirty-csv-by-removing-duplicates` | `csv-dedup-null-cleaner` |

## Few-Shot Examples

Task: "Parse a CSV file and return row count and column names."
Answer: classified_type=mcp_tool, confidence=0.95, rationale="Single \
deterministic computation — parse file, extract metadata, return result.", \
capability_name="csv-row-column-summarizer"

Task: "Clean a dirty CSV by removing duplicates and rows with null values."
Answer: classified_type=mcp_tool, confidence=0.9, rationale="Deterministic \
data cleaning operation — filter and dedupe in a single pass.", \
capability_name="csv-dedup-null-cleaner"

Task: "Build an inventory reorder pipeline using inventory CSV and reorder \
rules YAML. Compute days_of_stock, identify products below threshold, \
compute reorder quantities capped at max_order_value, sum total cost."
Answer: classified_type=skill, confidence=0.9, rationale="Prescribed domain \
workflow with reorder rules, thresholds, and capping logic. The 'how to \
approach inventory reorder' knowledge is the value.", \
capability_name="inventory-reorder-calculator"

Task: "Build a data integration pipeline joining sales CSV with product \
Excel and regional targets. Compute per-region revenue and compare against \
targets."
Answer: classified_type=skill, confidence=0.9, rationale="Multi-source \
integration following a known procedure: join, aggregate, compare. Domain \
knowledge of how to combine these sources is prescriptive.", \
capability_name="multi-source-revenue-integrator"

Task: "Investigate a service degradation incident. Examine metrics around \
the onset time, identify which metric changed first (root cause), and \
determine the cascade chain by comparing onset times across metrics. Requires \
adaptive investigation."
Answer: classified_type=agent, confidence=0.95, rationale="Open-ended \
investigation — the path depends on what anomalies are found. Cannot be \
prescribed because hypothesis testing drives the next steps.", \
capability_name="cascade-root-cause-investigator"

Task: "Autonomously profile a messy dataset, identify data quality issues, \
investigate clusters and correlations, and report findings."
Answer: classified_type=agent, confidence=0.9, rationale="Adaptive \
investigation where the analysis path depends on what the profiling reveals.", \
capability_name="dataset-quality-profiler"

## Task to Classify

Full task description:
{task_description}

Classify this task. Respond with:
1. `classified_type` — the architectural layer (mcp_tool, skill, or agent)
2. `confidence` — 0.0 to 1.0
3. `rationale` — brief explanation of why this type fits
4. `capability_name` — kebab-case name following the rules above\
"""
