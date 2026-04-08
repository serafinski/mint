# Verified Just-in-Time Capability Synthesis for Self-Developing Multi-Agent Systems

**Master Thesis Research Proposal**  
**Domain:** Context Engineering in LLM-Powered Multi-Agent Systems  
**Date:** February 2026

---

## The Bootstrapping Problem

The ambition behind self-developing MAS is that agents don't merely execute plans — they build the *infrastructure* to execute those plans. Given a complex task (e.g., a business analytics pipeline requiring ML training and inference), the system must analyze the task, generate an action plan, identify what skills, tools, agents, and MCP servers are required, detect the gaps between what already exists and what's needed, and then generate the missing capabilities on the fly.

This creates a bootstrapping loop: the system must be capable enough to *build the capabilities it doesn't yet have*. Three fundamental tensions emerge from this loop:

**Autonomy vs. correctness.** LLM agents optimize for immediate functionality — silently injecting mocks, hardcoding responses, creating fallback paths that mask failures. The more autonomous the generation, the harder it is to guarantee the generated artifacts actually work in production.

**Specificity vs. generality.** A highly specific skill (e.g., "parse semicolon-delimited SAP CSV exports into Pandas DataFrames") is reliable but gets invoked once. A general skill (e.g., "handle data ingestion") is reusable but hallucinates on edge cases. Every practitioner navigates this tradeoff intuitively; no principled framework exists for optimizing it.

**Context economy.** In a MAS where agents pass context to each other, naive approaches (full conversation history) hit token limits and introduce noise. But aggressive compression loses critical details. The system needs context engineering that preserves exactly the information each downstream agent requires — no more, no less.

This thesis proposes an architecture that addresses all three tensions through a unified three-pillar design.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Task Input                            │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│              PILLAR 1: JIT Capability Pipeline           │
│                                                          │
│  Task Decomposition → Skill Gap Detection →              │
│  Capability Classification → Generation                  │
│                                                          │
│  Outputs: MCP Tools / Agent Skills / New Agents          │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│              PILLAR 2: Verification Layer                │
│                                                          │
│  Tier 1: Static Scan (pattern detection)                 │
│  Tier 2: Behavioral Testing (edge-case probing)          │
│  Tier 3: Adversarial Probing (red-team agent)            │
│                                                          │
│  Gate: Only verified capabilities enter production       │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│              PILLAR 3: Granularity Optimizer             │
│                                                          │
│  Runtime Metrics → Reuse/Success/Cost/Maintenance →      │
│  Split (too general) / Merge (too specific) →            │
│  Evolutionary pressure toward optimal granularity        │
└─────────────────────────────────────────────────────────┘
```

---

## Pillar 1: The JIT Capability Pipeline

### Problem

When a self-developing MAS encounters a task requiring a capability it doesn't possess, it currently has two options: fail, or ask a human. Neither scales. A third option — generating the capability at runtime — is feasible with modern code-generation models but raises questions about what form the generated capability should take and how it integrates with the existing system.

### Approach

The pipeline operates in five stages:

1. **Task decomposition.** An orchestrator agent (using a deep-thinking model with a large token budget) breaks the input task into subtasks, each annotated with the capabilities required to complete it.

2. **Skill gap detection.** Each required capability is matched against a skill registry — a semantically indexed library of existing tools, skills, and agents. Gaps are identified as subtasks with no match above a confidence threshold. The registry uses hierarchical embeddings (domain → capability → specific skill) to prevent both false positives (superficial keyword matches) and false negatives (semantically equivalent but differently named skills).

3. **Capability classification.** Each gap is classified to determine what *kind* of artifact should be generated:
   - **Deterministic + stateless** → Generate an **MCP tool** (a Python/TypeScript function wrapped in an MCP server interface, exposing typed input/output schemas via JSON-RPC)
   - **Procedural + domain-specific** → Generate an **Agent Skill** (a SKILL.md file with instructions, templates, and optional scripts that teach an agent how to perform a specialized workflow)
   - **Stateful + requires planning** → Spawn a **new agent** with an appropriate system prompt and model configuration (selected via TOML-based provider-agnostic config)

   This classification prevents the common mistake of treating all generated capabilities as the same kind of artifact. An MCP tool that should have been a skill will be too rigid; a skill that should have been an agent will lack the autonomy to handle state.

4. **Generation.** A code-generation agent (Claude Code or equivalent) produces the artifact. For MCP tools, this means writing the function, the server wrapper, and a test harness. For Skills, it produces the SKILL.md with YAML frontmatter and the instruction body. For agents, it generates the system prompt and configuration entry.

5. **Sandboxed integration.** The generated artifact is deployed into an isolated environment — a sandboxed MCP server, a skill folder, or a contained agent session — and connected to the orchestrator for immediate use.

### Structured State Communication

Agents within the pipeline communicate via strictly typed JSON schemas representing the "world state" at each handoff point. This replaces natural-language summaries (lossy, prone to hallucination) and raw conversation histories (noisy, token-expensive) with a protocol-buffer-style contract that each agent can parse deterministically. The schema evolves as the pipeline discovers new capability types, but at any given moment it provides a stable interface between stages.

### Key Research Questions

- How accurately can an LLM classify capability gaps into the three artifact types (MCP tool vs. Skill vs. Agent)?
- What is the success rate of JIT-generated capabilities compared to pre-built, human-designed equivalents?
- How does the latency of generation + verification compare to the cost of failing and escalating to a human?

---

## Pillar 2: The Verification Layer

### Problem

LLM agents generating code systematically optimize for "making things work" in the immediate context rather than producing correct, production-grade artifacts. Common failure modes include:

- **Hardcoded responses:** The generated tool returns plausible-looking data without actually querying any external source.
- **Silent fallbacks:** The tool wraps API calls in broad exception handlers that return synthetic data when the real call fails.
- **Mock contamination:** Test fixtures leak into production paths, making tests pass while real usage fails silently.
- **Graceful-looking degradation:** The tool produces outputs of the right *shape* (correct column names, plausible value ranges) but with fabricated content.

These patterns are especially dangerous in a JIT pipeline because the generated artifacts are used immediately, often without human review.

### Approach: Three-Tier Verification

Every generated capability must pass all three tiers before entering the production skill registry.

**Tier 1 — Static Analysis (Pattern Detection)**

A zero-dependency scanner runs regex-based and AST-based pattern detection across the generated code. It checks for:

| Category | Patterns Detected | Severity |
|---|---|---|
| Hardcoded data | String literals resembling API responses, embedded JSON blobs, inline data arrays | Critical |
| Silent exception handling | Bare `except:` blocks, `except Exception: return default`, fallback return values in error paths | High |
| Mock artifacts | `unittest.mock` imports outside test files, `@patch` decorators, `MagicMock` instances | Critical |
| Synthetic data indicators | `random.seed()` with fixed values, `faker` library usage in non-test code, lorem ipsum strings | Medium |
| Missing external calls | Functions that declare API parameters but never make network requests | High |

The scanner produces a severity-ranked report. Any "Critical" finding blocks deployment immediately — the capability is rejected without proceeding to Tier 2 or Tier 3, which are significantly more expensive. "High" findings require Tier 2 clearance. This tiered cost structure is a deliberate design choice: static analysis is near-zero cost, behavioral testing requires LLM invocations and sandbox execution, and adversarial probing requires multi-step autonomous reasoning. Running all three tiers on every artifact regardless of Tier 1 results would waste compute on capabilities that can be rejected deterministically.

**Tier 2 — Behavioral Testing (Edge-Case Probing)**

A verifier agent generates a suite of test inputs designed to expose mock contamination:

- **Nonexistent entity tests:** Request data for entities that cannot exist in any real source (e.g., a company founded in 2087, a stock ticker with 15 characters). A legitimate tool should return an error or empty result; a mock-contaminated tool will return plausible-looking fabricated data.
- **Consistency tests:** Call the tool twice with identical inputs. Legitimate tools return identical results; tools relying on random generation or LLM improvisation will produce variance.
- **Boundary tests:** Provide inputs at extreme edges (empty strings, maximum-length values, special characters). Legitimate tools have defined behavior; shortcuts tend to break or reveal default responses.
- **Latency analysis:** Measure response time distribution. Hardcoded responses are suspiciously fast and uniform; real API calls show network-characteristic variance.

**Tier 3 — Adversarial Probing (Red-Team Agent)**

A dedicated red-team agent attempts to make the generated capability produce incorrect but plausible output. It:

- Crafts inputs designed to trigger known LLM shortcut patterns (e.g., asking for "sample data" or "example output" to see if the tool has a hidden demo mode)
- Compares outputs against known ground truth where available
- Tests whether the tool behaves differently when network connectivity is simulated as unavailable (legitimate tools should fail clearly; mock-contaminated tools continue producing output)

### Verification as a Research Contribution

No published work on self-evolving agents (EvoMAC, EvoAgentX, STELLA, AgentEvolver, ACE) includes a systematic verification layer for generated capabilities. STELLA's Tool Creation Agent generates and tests tools in biomedical domains but relies on functional correctness tests, not adversarial probing for the specific failure modes that LLM code generation produces. This verification pipeline, grounded in empirically observed contamination patterns, is a novel contribution.

---

## Pillar 3: The Granularity Optimizer

### Problem

After running for many task cycles, the skill registry accumulates capabilities at arbitrary granularity levels. Some are too general (high reuse but inconsistent success), others too specific (perfect success but invoked once and forgotten). Without optimization pressure, the registry grows unboundedly while effective coverage stagnates.

### Approach: Evolutionary Feedback Loop

After each task execution cycle, four metrics are computed for every capability in the registry:

| Metric | What It Measures | Signal |
|---|---|---|
| **Reuse rate** | Fraction of recent tasks that invoked this capability | Is it general enough to be useful? |
| **Success rate** | Fraction of invocations producing verified correct output | Is it reliable? |
| **Token cost** | Context window tokens consumed by the capability's definition (schema for MCP tools, instruction text for Skills) | Is it efficient? |
| **Maintenance burden** | How often the capability has required regeneration, patching, or version updates | Is it stable? |

These metrics drive three evolutionary operations, scoped by artifact type to match what LLMs can reliably perform:

**Split (capability is too general).** When a capability shows high reuse but mediocre success (say, >60% reuse, <70% success), it is decomposed into two or more specialized variants. The optimizer agent analyzes failure cases to identify the input subspaces where the capability breaks, then generates targeted variants for each subspace. Split applies to Skills and Agent configurations, where the operation amounts to refining instructions or system prompts. For MCP tools, splitting would require automated code refactoring — a problem beyond reliable LLM capability — and is deferred to future work.

**Merge (capabilities are too specific).** When two or more capabilities show very low reuse (<5%) but high similarity (measured by embedding distance of their descriptions and overlapping input schemas), they are merged into a single, broader capability. The merged version inherits the strongest behavioral patterns from each parent. Merge applies to Skills, where combining instruction sets is a natural LLM strength. Merging MCP tool source code is excluded from the implementation for the same reason as splitting, though the formal framework applies uniformly.

**Prune (capability is dead weight).** Capabilities with zero invocations over a configurable window and no declared dependencies are retired from the active registry (archived, not deleted, for potential resurrection). Prune applies to all artifact types — it requires no code transformation, only a registry state change.

The formal framework (utility function, convergence analysis) is artifact-type-agnostic. The implementation scopes operations to where LLMs can perform them reliably, and the thesis discusses the full framework alongside the scoped implementation.

### Formal Framework

Define the *utility* of a capability $c$ as:

$$U(c) = \alpha \cdot \text{reuse}(c) \cdot \text{success}(c) - \beta \cdot \text{token\_cost}(c) - \gamma \cdot \text{maintenance}(c)$$

where $\alpha$, $\beta$, $\gamma$ are weights tunable to the deployment context (e.g., token-constrained environments increase $\beta$; safety-critical domains increase the implicit threshold on success rate).

The optimization objective is to maximize total registry utility $\sum_c U(c)$ subject to coverage constraints (every task type in the expected distribution must be serviceable by at least one capability).

This creates a "Darwinian" pressure: capabilities compete for registry space based on demonstrated value. Over simulation cycles, the registry converges toward a Pareto-optimal distribution of granularity for the actual task distribution the system encounters.

### Key Research Questions

- Does evolutionary optimization produce meaningfully different granularity distributions than human-designed skill libraries?
- How many task cycles are required for the registry to converge?
- Is the converged distribution stable under task distribution shift?

---

## Evaluation Strategy

### Testbed: Business Analytics Pipeline

The architecture is evaluated on a Business Analytics Pipeline testbed: complex analytics tasks requiring data ingestion from heterogeneous sources, data transformation, ML model training, inference, and report generation. This testbed was chosen because it naturally produces diverse capability types — deterministic data ingestion tools (MCP tools), domain-specific transformation workflows (Skills), and stateful analysis agents (Agents) — which exercises all three artifact classifications in the capability pipeline. The tasks span multiple difficulty levels, from trivially simple (e.g., string manipulation, basic statistics) to genuinely complex (e.g., multi-source joins with schema reconciliation), ensuring the evaluation always produces analyzable data even if the most complex tasks fail.

An alternative testbed — a MAS-based factory simulation with scheduling optimization, breakdown diagnosis, and quality control — was considered but deferred. The factory simulation offers better repeatability but less diversity in required capability types, which is the primary dimension this thesis investigates.

### Baselines

| Baseline | Description |
|---|---|
| **Static skills** | A fixed, hand-curated library with no JIT generation. Measures the ceiling of human-designed capabilities. |
| **JIT without verification** | The generation pipeline (Pillar 1) runs without the verification layer (Pillar 2). Measures how much verification actually contributes. |
| **JIT without granularity optimization** | Pillars 1 + 2 run, but generated capabilities accumulate at whatever granularity they were born with. Measures whether evolutionary pressure matters. |
| **Full system** | All three pillars active. |

### Metrics

| Metric | What It Captures |
|---|---|
| **End-to-end task success rate** | Does the system actually solve the tasks? |
| **Mock contamination rate** | What fraction of generated capabilities contain hardcoded/fabricated elements? (Measured by manual audit of a sample.) |
| **Capability reuse distribution** | Gini coefficient of invocation counts across the registry. Healthy registries have moderate inequality; unhealthy ones have extreme skew. |
| **Token cost per task** | Total tokens consumed including capability definitions, generation, verification, and execution. |
| **Time to first correct execution** | Latency from task input to verified correct output, including any JIT generation time. |
| **Registry convergence** | Number of cycles until the granularity distribution stabilizes (measured by KL divergence between consecutive registry snapshots). |

---

## Proposed Thesis Structure

```
Chapter 1  Introduction
  1.1  The Bootstrapping Problem in Self-Developing MAS
  1.2  Three Tensions: Autonomy vs. Correctness, Specificity vs. Generality, Context Economy
  1.3  Contributions and Thesis Outline

Chapter 2  Related Work
  2.1  Self-Evolving Agent Systems (EvoMAC, EvoAgentX, AgentEvolver, STELLA)
  2.2  Context Engineering (ACE Framework, Google ADK Architecture, Write/Select/Compress/Isolate)
  2.3  Tool and Skill Generation (ToolLLM, ATLASS, Agent Skills Specification)
  2.4  Multi-Agent Coordination (Claude Code Agent Teams, GPTSwarm, G-Designer)
  2.5  Positioning: What This Thesis Adds

Chapter 3  The JIT Capability Pipeline
  3.1  Task Decomposition and Skill Gap Detection
  3.2  Capability Classification Framework (MCP Tool vs. Skill vs. Agent)
  3.3  Generation Pipeline
  3.4  Structured State Communication Protocol
  3.5  Implementation with LangGraph, MCP, and Claude Code

Chapter 4  Verified Capability Synthesis
  4.1  Taxonomy of LLM Code Generation Failure Modes
  4.2  Tier 1: Static Analysis Scanner
  4.3  Tier 2: Behavioral Testing Protocol
  4.4  Tier 3: Adversarial Probing Agent
  4.5  Verification Gate: Integration with the Pipeline

Chapter 5  Granularity Optimization
  5.1  The Specificity-Generality Spectrum: Formal Framework
  5.2  Four-Metric Evaluation System
  5.3  Evolutionary Operations: Split, Merge, Prune
  5.4  Convergence Analysis

Chapter 6  Evaluation
  6.1  Testbed Description
  6.2  Baselines and Ablation Design
  6.3  Results
  6.4  Analysis and Discussion

Chapter 7  Conclusion and Future Work
  7.1  Summary of Contributions
  7.2  Limitations
  7.3  Future Directions: Context Rot in Long-Running MAS,
       Adversarial Debate as Coordination Mechanism,
       Cross-Protocol Interoperability (MCP + A2A)
```

---

## Tech Stack Mapping

| Architecture Component | Technology |
|---|---|
| Orchestration & agent graphs | LangGraph / Google ADK |
| Code generation for capabilities | Claude Code |
| Tool serving (generated MCP tools) | MCP SDK (Python/TypeScript) |
| Agent-to-agent communication | A2A protocol / LangGraph message passing |
| Model configuration & provider switching | TOML-based config with provider-agnostic factories |
| Skill packaging | Agent Skills specification (SKILL.md + resources) |
| Verification scanner | Zero-dependency Python (CI-integrable) |
| Sandbox execution | Docker containers / subprocess isolation |
| Skill registry | Semantic search over embeddings (e.g., ChromaDB) |

---

## Why This Thesis

| Criterion | Assessment |
|---|---|
| **Novelty** | No published work combines JIT capability generation with a verification layer and granularity optimization. STELLA generates tools without verifying them. EvoMAC evolves agent connections without generating new tools. ACE evolves context without evolving capabilities. This thesis fills the gap between all three. |
| **Feasibility** | Each pillar is independently buildable and testable. Pillar 1 (pipeline) can be prototyped in weeks with LangGraph + Claude Code. Pillar 2 (verification) extends existing scanner patterns. Pillar 3 (granularity) can start with simple heuristics and add evolutionary pressure incrementally. |
| **Practical value** | Every component is immediately deployable. The verification pipeline alone improves the safety of any system using LLM-generated code. The granularity framework provides a decision tool for any team building skill libraries. The JIT pipeline eliminates manual tool creation for recurring capability gaps. |
| **Publication potential** | Three distinct contributions, each publishable as a workshop or short paper: (1) compositional JIT capability pipeline, (2) adversarial verification protocol for LLM-generated tools, (3) formal granularity optimization framework. Together they compose a strong thesis. |

---

## Key References

- Zhang, Q. et al. (2025). *Agentic Context Engineering: Evolving Contexts for Self-Improving Language Models.* arXiv:2510.04618.
- EvoMAC (2025). *Self-Evolving Multi-Agent Collaboration Networks.* ICLR 2025.
- Zhai, Y. et al. (2025). *AgentEvolver: Towards Efficient Self-Evolving Agent System.* arXiv:2511.10395.
- Wang, Y. et al. (2025). *EvoAgentX: An Automated Framework for Evolving Agentic Workflows.* arXiv:2507.03616.
- STELLA (2025). *Self-Evolving LLM Agent for Biomedical Research.* bioRxiv:2025.07.01.662467.
- Fang, J. et al. (2025). *A Comprehensive Survey of Self-Evolving AI Agents.* arXiv:2508.07407.
- Google ADK (2025). *Architecting Efficient Context-Aware Multi-Agent Framework for Production.* Google Developers Blog.
- Martin, L. (2025). *Context Engineering for Agents.* Blog post.
- ACM TOSEM (2025). *LLM-Based Multi-Agent Systems for Software Engineering.* doi:10.1145/3712003.
- Anthropic (2025). *Code Execution with MCP: Building More Efficient AI Agents.* Engineering blog.
- Anthropic (2025). *Agent Skills Specification.* agentskills.io.
