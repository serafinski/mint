"""Prompt for the per-gap capability classifier (LLM-based, temperature=0).

Classification is based on three architectural layers (industry consensus):
- MCP Tool = execution layer (atomic action, stateless, deterministic)
- Skill = knowledge layer (procedural expertise, prescribed workflow)
- Agent = reasoning layer (autonomous, adaptive, unpredictable path)

As of Stage 2 of the compositional refactor, the classifier decides a type
for a SINGLE gap (one step of the workflow), not for the whole task. The
task description is supplied as context so the classifier can reason about
what the gap's role is within the larger workflow.

References: MCP spec (modelcontextprotocol.io), Agent Skills spec
(agentskills.io), Anthropic "Building Effective Agents", LangGraph docs.
"""

# Per-gap classification prompt (Stage 2+). One LLM call classifies ONE gap
# at a time, in the context of the whole task.
GAP_CLASSIFICATION_PROMPT = """\
You are a capability classifier for a JIT synthesis pipeline. Your job is to \
classify ONE STEP (a "gap") of a larger task into one architectural layer \
based on what kind of capability best implements that step.

The critical framing: **you are NOT classifying the step in isolation. You \
are classifying the step WITHIN ITS PARENT TASK.** A step inherits meaning \
from the task it serves. Classifying a gap purely on its own phrasing — \
"is this a single function?" — defaults everything to mcp_tool because \
narrow steps almost always admit a single-function implementation. That \
answer is technically true but misses the point: a skill-task's specialized \
expertise lives inside its steps, not beside them.

## The Three Layers

These are complementary architectural layers, not points on a complexity axis. \
Each solves a fundamentally different problem. The definitions below follow \
the thesis proposal (``docs/thesis_proposal.md``, Pillar 1 §3) and the \
industry specs it references.

### mcp_tool — The Execution Layer
An MCP tool is a **single, well-defined operation exposed with a typed \
input/output contract**. Per the MCP specification, a tool has a name, a \
description, an input schema, and an optional output schema, and is \
invoked as a callable function by a language model. Tools perform \
actions and return results — "querying databases, calling APIs, or \
performing computations" are all canonical MCP tool examples.

The thesis proposal frames the mcp_tool category as "deterministic + \
stateless" — the *ideal* case where the tool's output is a pure function \
of its inputs. In practice the MCP spec is broader than that (tools may \
consult internal state or reference data), but for classification \
purposes the distinguishing question is: **is the value of this capability \
in executing a well-specified operation, rather than in packaging \
specialized expertise or in autonomous planning?**

Examples: parsing a file, computing a hash, validating a schema against \
an input, joining two datasets, summing numbers by key, formatting a \
report table, sorting a list, computing a chargeable weight from a \
formula, looking up a record in a reference table.

### skill — The Knowledge Layer
A skill is **packaged specialized expertise** that an agent loads to \
perform a domain-specific task correctly. Per Anthropic's Agent Skills \
specification, a skill is "organized folders of instructions, scripts, \
and resources that agents can discover and load dynamically to perform \
better at specific tasks" — **the packaging itself is the value**. Per \
the thesis proposal, skills are the "procedural + domain-specific" \
layer, delivered as a SKILL.md with instructions, templates, and \
optional scripts.

The packaged expertise can take many forms:

- **Prescribed workflow / SOP** — the right sequence of steps and \
  validations for a domain task (e.g., how to onboard a new employee, \
  how to triage a bug report, how to run a structured code review).
- **Reference procedure with rules/thresholds** — a procedure whose \
  correctness depends on applying external rules, caps, eligibility \
  criteria, tariffs, or allocations (e.g., loan underwriting against an \
  eligibility policy, shipping rate calculation against carrier \
  tables).
- **Domain vocabulary / template / style guide** — correct \
  terminology, formatting, or voice (e.g., translating clinical notes \
  to ICD codes, writing in a brand voice, producing code review \
  comments in the team's preferred format).
- **Reference-material-driven answering** — tasks where the right \
  answer comes from applying domain references correctly (e.g., \
  answering legal questions using a specific case-law corpus, writing \
  SQL against a specific schema convention).

A skill is NOT defined by having a rules file, and is NOT defined by \
whether a sufficiently capable LLM could get the answer right without \
packaging. A task is a skill-task whenever it is shaped as a packaged \
domain procedure — the packaging is the architectural choice the \
classifier is recognising, not a difficulty assessment.

### agent — The Reasoning Layer
An agent is an **autonomous system that dynamically directs its own \
process**. Per Anthropic's "Building Effective Agents", agents are \
"systems where LLMs dynamically direct their own processes and tool \
usage, maintaining control over how they accomplish tasks" — the \
distinguishing property is dynamic control flow, not predefined code \
paths. Per the thesis proposal, agents are the "stateful + requires \
planning" layer. An agent makes decisions and adjusts strategy based \
on intermediate findings, pursuing open-ended goals where the path to \
completion is unpredictable.

Examples: clinical triage (examine vitals, form hypotheses, revise), \
legal contract risk review (trace clause dependencies, score \
exposure), exploratory dataset profiling (decide what to investigate \
based on what the current analysis reveals), multi-system failure \
troubleshooting.

## Decision Process — Parent-Task First, Then Step-in-Context

**Step 1: Classify the PARENT TASK first.** Read the full task description \
and apply the following checklist IN ORDER. Stop at the first match.

**Is the parent task an agent-task?** Answer YES if ANY of these signals \
are present:
- The task uses investigative or open-ended language: ``investigate``, \
  ``diagnose``, ``trace``, ``triage``, ``explore``, ``review for ...``, \
  ``troubleshoot``, ``profile autonomously``, ``form hypotheses``, \
  ``examine and revise``, ``determine the cause``, ``find the source \
  of``.
- The task explicitly says the analysis path depends on intermediate \
  findings, requires hypothesis testing, or needs adaptive reasoning.
- The task describes an open-ended goal where the steps cannot be \
  prescribed in advance because intermediate results change what \
  should happen next.

If yes → **agent-task**. Stop.

**Is the parent task a skill-task?** Answer YES if ANY of these \
**packaging signals** are present (this is a checklist of how the task \
is shaped, NOT a difficulty test):

1. **Named domain procedure.** The task description names a domain \
   procedure: ``X reconciliation``, ``X underwriting``, ``X reorder``, \
   ``X adjudication``, ``X validation pipeline``, ``X allocation``, \
   ``X eligibility``, ``X onboarding``, ``X compliance check``, \
   ``X review``, or any other named domain workflow.
2. **Policy / rules / reference input.** The task supplies a \
   configuration input that drives the logic — a file, a parameter, a \
   reference table, a rules dict, a policy object — carrying \
   thresholds, caps, tariffs, allocations, eligibility criteria, \
   pricing tables, or any domain-specific values that determine \
   correct behaviour. Format and filename do not matter.
3. **Domain vocabulary / template / style guide.** The task requires \
   output to conform to a specific domain vocabulary, style guide, \
   format convention, brand voice, taxonomy, or template.
4. **External reference / domain knowledge base.** The task requires \
   applying an external reference, taxonomy, code list, lookup table, \
   or domain dictionary that supplies the substantive content of the \
   answer.

If ANY of 1-4 is present → **skill-task**. Stop.

**Otherwise → tool-task.** A tool-task is a task with NO investigative \
signals AND NO packaging signals — pure deterministic transformations, \
parsing, aggregation, formatting, or computation where the work has no \
named domain wrapper and no external policy driving the logic.

**Critical: this is a packaging-signal checklist, not a difficulty \
assessment.** Do not ask "could a sufficiently capable LLM solve this \
without the packaging?". The right question is "is this task shaped \
as a packaged domain procedure?". A budget reconciliation pipeline \
with a rules file is a skill-task even if every individual step is \
mechanically simple — the packaging is the architectural choice the \
classifier is recognising.

**Step 2: Classify THIS GAP within the parent task.**

- If the parent is an **agent-task**, the whole thing is meant to be ONE \
agent capability. If you see multiple gaps for an agent-task, the \
decomposer over-split — classify every gap as agent so downstream \
deduplication collapses them into one agent artifact. Do not split an \
adaptive investigation into tool steps.

- If the parent is a **skill-task**, ask: **does THIS gap carry the \
task's specialized expertise, or is it infrastructure beneath it?**
  - **Skill gap**: the step is where the packaged expertise actually \
    applies. The step's correctness depends on domain knowledge — a \
    prescribed procedure, an applied rule, a reference lookup, a \
    domain vocabulary, a style/template. Signal words in the gap name \
    or description include "apply-*", "enforce-*", "assess-*", \
    "adjudicate-*", "triage-*", "classify-against-*", \
    "transform-per-*", or any reference to the task's domain-knowledge \
    input.
  - **Tool gap**: the step is pure plumbing — parsing an input, summing \
    a column, formatting output, joining datasets, sorting — and would \
    be implemented identically in a task with completely different \
    domain expertise. These are legitimate mcp_tools even inside a \
    skill-task.
  - **A well-decomposed skill-task usually produces a MIX of mcp_tool \
    and skill gaps.** If you find yourself classifying every gap of a \
    skill-task as mcp_tool, you are ignoring the step that carries the \
    specialized expertise — re-read the gap names and description and \
    find it. Conversely, if every gap classifies as skill, you are \
    probably missing the plumbing steps that are legitimately tools.

- If the parent is a **tool-task**, every gap is mcp_tool. A pure \
compute pipeline doesn't have skill gaps or agent gaps.

**Do NOT** lead with "is this step a single deterministic function?" That \
test is too permissive — it fires for almost any narrow gap, including \
gaps that exist precisely to apply a skill-task's specialized expertise. \
Lead with the parent-task assessment instead.

**File-writer exception (Phase 3 Step 12, 2026-04-16).** One carve-out \
to the parent-task-first rule: **a subtask whose job is to serialize \
upstream data to a file is ALWAYS `mcp_tool`, regardless of the parent \
task's type.** Signals that identify a file-writer subtask:

- Description explicitly names a filename and a file format (e.g., \
"Write the summary to output.xlsx as Excel", "Save report.md", \
"Emit output.csv")
- `required_capabilities` contains a `write-<ext>-file` / \
`write-<format>-report` / `save-<format>` capability name
- `output_schema` has an `output_file` key

For such a subtask, the Pillar 1.3 rule applies literally: writing a \
serialized dict to a file on disk is deterministic + stateless. It also \
has a **structural** reason to be `mcp_tool` — the SKILL and AGENT \
execution backends cannot write to the Docker workspace where \
`_collect_output_files` picks files up. A misclassified writer will \
silently fail to produce the file even when the rest of the chain \
succeeds. See Example G below for the canonical case.

This exception OVERRIDES the "agent-task with N gaps → classify all \
as agent" collapsing rule. An investigation-style agent-task that \
ends in a file writer produces TWO capabilities: one AGENT for the \
adaptive investigation, one MCP_TOOL for the file write — NOT one \
lead+subagents agent with a writer subagent.

## Important Distinctions

- **Multi-step ≠ skill.** A tool-task with multiple compute stages is still \
a tool-task; each stage is mcp_tool.
- **Single-function ≠ mcp_tool** when the gap's whole reason for \
existing is to carry the task's specialized expertise. A one-line \
`return style_guide.apply(draft)` is a skill-gap when ``style_guide`` \
is the packaged expertise the skill provides, regardless of how terse \
the call site is.
- **Skill ≠ rules file.** A skill may be driven by a policy file, but \
skills that package a workflow, a style guide, a domain vocabulary, or \
a reference procedure are equally valid. Don't require a rules file to \
classify something as a skill.
- **"Workflow" language ≠ skill.** Many tool-tasks describe themselves \
as workflows but carry no specialized expertise — a three-stage \
compute pipeline with no domain knowledge is still a tool-task.
- **Agent-task with N gaps ≠ N agents.** Agent-tasks should ideally be \
one gap; if you see multiple, classify the adaptive-reasoning gaps as \
agent so they collapse via downstream deduplication. **Exception:** a \
file-writer gap at the end of the chain is `mcp_tool`, not \
agent — see the "File-writer exception" in the Decision Process \
section above and Example G below.
- Classify EACH GAP INDIVIDUALLY within the parent's context. Two gaps \
in the same skill-task CAN be different types (typically a mix of \
mcp_tool plumbing and skill expertise-carrying steps).

## Full Task Context

{task_description}

The decomposer identified these required capabilities for the whole task:
{required_capabilities}

## The Specific Gap to Classify

You are classifying THIS single capability (one step of the workflow above):

    {gap_query}

Gap description: {gap_description}

Classify this gap. First, internally assess the parent task's type using \
Step 1 above. Then, using Step 2, decide what THIS gap is within that \
parent. Your rationale MUST name the parent-task assessment explicitly \
(e.g., "Parent task is a skill-task (packaged underwriting expertise); \
this gap carries the expertise → skill" or "Parent task is a tool-task \
(pure compute pipeline); this gap is a plain aggregation → mcp_tool").

## Capability Naming

You must also propose a short, semantic `capability_name` for the generated \
capability that implements this gap. This name will be used as the registry \
identifier and should describe WHAT THE CAPABILITY DOES, not how the task was \
phrased.

### Naming rules
- **Kebab-case** (lowercase letters, digits, and hyphens only)
- **3-5 words**, maximum 40 characters
- **Describe the capability, not the task prompt**. A good name is reusable \
across semantically similar steps.
- **Avoid task-specific verbs** like "parse-this", "build-a", \
"you-are-investigating", "read-the".
- **Avoid one-shot specificity**. Use general concepts the capability \
could be reused for.
- **Prefer reusable nouns over specific verbs**: `pdf-invoice-extractor` \
(reusable) vs `parse-this-pdf-invoice-file` (one-shot).
- **Name the GAP, not the whole task**. If the gap is "parse the input" \
within a larger domain pipeline, the name describes the parsing step, \
not the whole pipeline.

## Few-Shot Examples

### Example A — skill-task with mixed gap types (rules-based expertise)

Full task: "Build a loan eligibility pipeline. Read applicant records \
and an underwriting policy (credit-score floor, debt-to-income ceiling, \
collateral haircut percentage). For each applicant, apply the \
underwriting policy to decide approve/deny and compute the approved \
principal. Return summary statistics."

Parent-task assessment: **skill-task**. Two packaging signals are \
present: (1) the task names a domain procedure ("loan eligibility \
pipeline" / "underwriting"), and (2) it supplies an underwriting \
policy input carrying three knobs (credit-score floor, \
debt-to-income ceiling, collateral haircut percentage). The packaging \
itself is the value — this is exactly the shape of a SKILL.md with \
instructions and a config block.

Gap A1: "parse-applicants-and-underwriting-policy"
Answer: classified_type=**mcp_tool**, confidence=0.95, rationale="Parent \
is a skill-task (packaged underwriting procedure), but THIS gap is \
pure plumbing — loading two inputs into structured form. The parsing \
step has no packaging signal of its own; it would be implemented \
identically in any task involving the same input formats.", \
capability_name="applicants-policy-loader"

Gap A2: "apply-underwriting-policy-to-applicants"
Answer: classified_type=**skill**, confidence=0.90, rationale="Parent \
is a skill-task. This gap IS the step where the packaged underwriting \
procedure applies — it consumes the policy input and the named \
domain procedure (compute approved principal under the haircut, \
gate by the credit-score floor and DTI ceiling). The packaging \
signal is concentrated in this step; this is the gap a SKILL.md \
would actually instruct an agent on.", \
capability_name="underwriting-policy-applier"

Gap A3: "aggregate-approval-statistics"
Answer: classified_type=**mcp_tool**, confidence=0.95, rationale="Parent \
is a skill-task, but THIS gap is a trivial aggregation over the \
decision records produced by the previous step — counts and sums \
with no packaging signal. The skill's expertise was applied upstream; \
this step is plain compute over its output.", \
capability_name="approval-stats-aggregator"

### Example B — skill-task with mixed gap types (style/template expertise)

Full task: "Generate a structured code-review comment for each diff \
hunk in a pull request, following the team's code-review style guide \
(voice: direct but constructive; format: one-line summary, then \
bullet-pointed observations, then optional suggestions; tags: \
[blocking] / [suggestion] / [nit]). Return a dict mapping hunk IDs to \
review comment strings."

Parent-task assessment: **skill-task**. The packaging signal is signal \
3 from the checklist: the task supplies a style guide carrying voice, \
format, and tag conventions that the output must conform to. There is \
no rules file with numeric thresholds — and that does not matter. A \
style guide is exactly the kind of packaged expertise a SKILL.md \
delivers via instructions and templates. Skill ≠ rules file.

Gap B1: "parse-pr-diff-into-hunks"
Answer: classified_type=**mcp_tool**, confidence=0.95, rationale="Parent \
is a skill-task (packaged code-review style guide), but THIS gap is \
pure plumbing — parsing a diff into hunks is a standard operation \
with no packaging signal. Would be implemented identically for any \
diff-processing task.", capability_name="pr-diff-hunk-parser"

Gap B2: "write-review-comment-per-style-guide"
Answer: classified_type=**skill**, confidence=0.90, rationale="Parent \
is a skill-task. This gap IS the step where the packaged style guide \
applies — turning a raw diff hunk into a comment that conforms to \
the team's voice, format, and tag conventions. The packaging signal \
is concentrated here; this is the gap whose correctness depends on \
the style guide being followed.", \
capability_name="styled-review-comment-writer"

Gap B3: "format-hunk-to-comment-map"
Answer: classified_type=**mcp_tool**, confidence=0.95, rationale="Parent \
is a skill-task, but THIS gap is a trivial dict construction — \
mapping hunk IDs to comment strings produced by the previous step. \
No packaging signal, just data shaping.", \
capability_name="hunk-comment-map-formatter"

### Example C — pure tool-task (single-gap, no specialized expertise)

Full task: "Compute the SHA-256 hash of a text file and return the \
hex digest."

Parent-task assessment: **tool-task** — single deterministic \
operation with no investigative signals and no packaging signals (no \
named domain procedure, no policy input, no style guide, no external \
reference).

Gap C1: "hash-text-file-sha256"
Answer: classified_type=**mcp_tool**, confidence=0.98, rationale="Parent \
is a tool-task. Single deterministic computation — read file, hash, \
return digest.", capability_name="sha256-file-hasher"

### Example D — multi-step tool-task (still all tools)

Full task: "Given a folder of image files, compute a perceptual hash \
for each image, group images whose hashes are within Hamming distance \
5, and return the groups sorted by size descending."

Parent-task assessment: **tool-task** — three pure compute stages \
(hash, cluster, sort) with no investigative signals and no packaging \
signals: no named domain procedure, no policy input, no style guide, \
no external reference table.

Gap D1: "phash-image-folder"
Answer: classified_type=**mcp_tool**, confidence=0.95, rationale="Parent \
is a tool-task. Perceptual hashing is a standard image operation — no \
specialized expertise needed.", capability_name="image-phash-computer"

Gap D2: "cluster-phashes-by-hamming-distance"
Answer: classified_type=**mcp_tool**, confidence=0.95, rationale="Parent \
is a tool-task. Hamming-distance clustering with a fixed threshold is \
a standard algorithm — no specialized expertise.", \
capability_name="hamming-distance-clusterer"

Gap D3: "sort-groups-by-size"
Answer: classified_type=**mcp_tool**, confidence=0.98, rationale="Parent \
is a tool-task. Plain sort by length — generic compute.", \
capability_name="groups-by-size-sorter"

All three gaps are mcp_tool because the parent task has no packaged \
specialized expertise — every step is generic compute.

### Example E — agent-task (stays one agent)

Full task: "Triage an incoming patient case: review the intake form \
and the recent vitals, identify the most likely acute condition, and \
recommend which specialist to page. Requires adaptive reasoning — \
examine inputs, form hypotheses, test them against symptoms, revise."

Parent-task assessment: agent-task — "triage", "form hypotheses", \
"examine, revise", open-ended with path dependent on intermediate \
findings.

Gap E1: "patient-case-triage"
Answer: classified_type=**agent**, confidence=0.95, rationale="Parent \
is an agent-task — adaptive triage whose reasoning path depends on \
what the intake and vitals reveal. Cannot be prescribed because \
hypothesis testing drives the next steps.", \
capability_name="patient-case-triager"

### Example F — agent-task with accidental over-decomposition

Full task: (same as Example E above)

If the decomposer over-split and produced two gaps:
Gap F1: "load-intake-form-and-vitals"
Gap F2: "identify-condition-and-recommend-specialist"

Both should classify as **agent** — not "F1 is mcp_tool, F2 is agent". \
The parent is an agent-task, and reading inputs is part of the agent's \
triage, not a separate tool step. Downstream deduplication will \
collapse them into one agent artifact.

Answer for F1: classified_type=**agent**, confidence=0.85, rationale="Parent \
is an agent-task. Reading the intake here is the agent's first \
investigative step, not a standalone tool — agent-tasks should not be \
split. Classifying as agent so downstream deduplication collapses this \
into the triage capability.", capability_name="triage-intake-reader"

Answer for F2: classified_type=**agent**, confidence=0.95, rationale="Parent \
is an agent-task; this is the core triage step.", \
capability_name="patient-case-triager"

### Example G — agent-task with a file-writer tail (File-writer exception)

Full task: "You are investigating a service degradation incident. Read \
the incident YAML and system metrics CSV, identify which metric changed \
first (the root cause), determine the cascade chain. Write a markdown \
incident summary to report.md. Also return a dict with \
'root_cause_metric', 'cascade_chain', 'n_metrics_investigated'."

The decomposer produced two gaps (Rule 15 compliance — the writer is a \
dedicated last step):
Gap G1: "incident-triage-agent"
Gap G2: "write-markdown-report"

The parent is an agent-task (adaptive investigation). **But the \
collapsing rule does NOT apply to G2** — G2 is the file-writer tail. \
Per the File-writer exception in Step 2 above, G2 is classified as \
mcp_tool even though the parent is an agent-task. G1 stays agent.

Answer for G1: classified_type=**agent**, confidence=0.95, \
rationale="Parent is an agent-task (adaptive incident investigation \
with hypothesis/revise loops). This gap is the investigation itself — \
classify as agent.", capability_name="incident-triage-agent"

Answer for G2: classified_type=**mcp_tool**, confidence=0.95, \
rationale="Parent is an agent-task, but this gap is the file-writer \
tail — it serializes upstream data to report.md. Per the File-writer \
exception, writers are always mcp_tool regardless of the parent's \
type (Pillar 1.3: file I/O is deterministic + stateless; also SKILL \
and AGENT backends cannot write to the Docker workspace). Classifying \
as mcp_tool so the chain ends in a deterministic writer that \
`_collect_output_files` can pick up.", \
capability_name="write-markdown-report"

**Result: a 2-step chain (AGENT investigator → MCP_TOOL writer), NOT \
a lead+subagents agent.** The writer runs in the Docker sandbox, \
writes `/workspace/report.md`, and the file is produced.

Contrast with Example F: F had no file-writer tail — both gaps were \
part of the investigation, so collapse was correct. Here G2 is \
structurally different: it's the post-investigation artifact \
serialization, not adaptive reasoning. Do NOT collapse.

## Respond With

1. `classified_type` — the architectural layer (mcp_tool, skill, or agent)
2. `confidence` — 0.0 to 1.0
3. `rationale` — brief explanation. **MUST start by stating the \
parent-task assessment** (tool-task, skill-task, or agent-task), then \
explain why THIS gap has its classified type within that parent context.
4. `capability_name` — kebab-case name following the rules above\
"""
