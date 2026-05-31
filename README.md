# MINT

Model-driven Instantiation of Novel Tools: a master's-thesis codebase for
just-in-time capability synthesis, verification, and registry evolution in a
self-developing multi-agent system.

The primary supported operator surface is the **evaluation protocol**:

- protocol wrappers: [`scripts/eval_protocol/`](scripts/eval_protocol/)
- chapter-ready outputs: [`docs/chapter6_numbers.md`](docs/chapter6_numbers.md), [`docs/chapter6_fact_pack.md`](docs/chapter6_fact_pack.md), [`docs/chapter6_evaluation_report.md`](docs/chapter6_evaluation_report.md)

## Overview

The system is organized around three thesis pillars:

1. **JIT capability pipeline**
   Generates missing capabilities on demand as one of three runtime types:
   `mcp_tool`, `skill`, or `agent`.
2. **Verification layer**
   Screens generated capabilities before registration using static checks,
   execution-backed checks, and adversarial probing.
3. **Granularity optimizer**
   Tracks capability utility and evolves the registry through operations such as
   merge and rollback.

The evaluation protocol exercises those pillars across:

- **Phase 1 cold-start breadth**: 20 tasks x 4 conditions x 3 runs
- **Phase 3b recurrence**: repeated task-family pressure on a warm registry
- **Phase 4 longitudinal**: 40-task warm-registry run for convergence / M6

## References
- [`docs/EVAL_PROTOCOL.md`](docs/EVAL_PROTOCOL.md): canonical Chapter 6 protocol
- [`scripts/eval_protocol/README.md`](scripts/eval_protocol/README.md): wrapper-stage flow
- [`pyproject.toml`](pyproject.toml): dependency groups and Python version
- [`.env.example`](.env.example): supported repo-level environment variables
- [`sandbox/Dockerfile`](sandbox/Dockerfile): sandbox runtime image

## Repository Layout

High-signal paths:

- [`src/orchestrator/`](src/orchestrator/): JIT orchestration pipeline
- [`src/verification/`](src/verification/): verification and sandbox execution
- [`src/optimizer/`](src/optimizer/): registry evolution and utility logic
- [`src/registry/`](src/registry/): Chroma and DuckDB-backed storage
- [`tests/evaluation/`](tests/evaluation/): task definitions, harness, fixtures
- [`scripts/`](scripts/): runnable evaluation and analysis scripts
- [`scripts/eval_protocol/`](scripts/eval_protocol/): enforced stage wrappers
- [`data/eval/`](data/eval/): live registries, DBs, generated skills, metrics stores
- [`docs/eval_runs/`](docs/eval_runs/): per-campaign captured protocol artifacts

## Prerequisites

Repository prerequisites:

- **Python** `>=3.11`
- **uv** for dependency and script execution
- **Docker** for sandboxed tool execution
- **OpenAI API access** for the default model path
- **LangFuse** if you want to run the full audit-gated protocol

Important operational note:

- The detached launch wrappers for Stage 1, Stage 3, and Stage 4 call
  `nohup caffeinate -i -s ...`, so the unattended protocol path currently
  assumes **macOS**.
- On Linux you can still run the underlying Python runners manually, but the
  wrapper scripts are written around `caffeinate` and should be treated as
  macOS-oriented unless you adapt them.

## Setup

### 1. Install dependencies

Base + dev + eval + analysis groups:

```bash
uv sync --group dev --group eval --group analysis
```

Those groups are defined in [`pyproject.toml`](pyproject.toml):

- `dev`: pytest, ruff
- `eval`: spreadsheet fixture support (`openpyxl`)
- `analysis`: plotting support (`matplotlib`)

### 2. Configure environment variables

Copy the example file:

```bash
cp .env.example .env
```

At minimum, set:

- `OPENAI_API_KEY`

For full protocol runs with trace audits, also set:

- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`
- `LANGFUSE_HOST`

Optional overrides already supported by the codebase:

- `LLM_MODEL` default: `openai:gpt-5-mini`
- `SANDBOX_IMAGE` default: `jit-sandbox`
- `JIT_ADVERSARIAL_REASONING` default: `low`
- `LANGFUSE_DISABLE_DURING_TESTS=true` to fully disable tracing in tests

See [`.env.example`](.env.example) for the supported repo-level env surface.

### 3. Build the sandbox image

The sandbox runtime is a Docker image named `jit-sandbox` by default.

Build it once:

```bash
docker build -t jit-sandbox sandbox
```

Sanity check that Docker is available:

```bash
docker ps
```

### 4. Verify the basic environment

Optional but recommended before a long run:

```bash
uv run pytest tests/unit/ -q
```

## Running the Evaluation Protocol

The protocol is a **state machine**, not just a checklist.

Each stage writes a marker in the run directory:

- `LAUNCHED`
- `SHAPE_OK`
- `PASS`
- `FAIL`

You should run the stages through the shell wrappers under
[`scripts/eval_protocol/`](scripts/eval_protocol/), not by improvising your own sequence.

### Recommended workflow

Pick one capture directory and keep reusing it:

```bash
RUN_DIR="docs/eval_runs/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN_DIR"
```

### Stage 0: preflight

```bash
scripts/eval_protocol/stage0_preflight.sh "$RUN_DIR"
```

What it does:

- runs `pytest tests/unit/`
- runs `scripts/test_pipeline.py`
- runs `scripts/test_verification.py`
- runs a 3-task longitudinal smoke
- writes an audit request if shape checks pass

To advance:

1. open the generated `stage0_audit_request.md`
2. complete the requested trace audit using a reviewer workflow with LangFuse trace access
3. save the response as `stage0_audit_verdicts.md`
4. rerun Stage 0

```bash
scripts/eval_protocol/stage0_preflight.sh "$RUN_DIR"
```

### Stage 1: main evaluation

```bash
scripts/eval_protocol/stage1_main_eval.sh "$RUN_DIR"
```

This launches the main evaluation matrix used by the thesis protocol:

- 20 tasks
- 4 conditions: `full`, `no_verification`, `no_optimization`, `static`
- 3 runs per cell
- total: 240 rows

The wrapper launches detached. Re-run the same command after the process exits:

```bash
scripts/eval_protocol/stage1_main_eval.sh "$RUN_DIR"
```

If shape checks pass, complete the generated LangFuse audit handoff and rerun again until the marker becomes `PASS`.

### Stage 2: headline numbers

```bash
scripts/eval_protocol/stage2_headline.sh "$RUN_DIR"
```

This stage produces the machine-readable headline metric artifacts:

- `20_analyzer_metrics.json`
- `21_sql_crosscheck.json`
- `22_agreement_summary.json`

Primary headline scope:

- `M1`
- `M3`
- `M5`
- `ClassAcc`

If agreement is clean, Stage 2 can auto-pass. If not, it will force a disagreement audit.

### Stage 3: supplemental recurrence track

```bash
scripts/eval_protocol/stage3_recurrence.sh "$RUN_DIR"
```

Default recurrence matrix:

- tasks: `T04,T11,T13,T14,T15,T20`
- variants: `3`
- conditions: `full,no_verification,no_optimization,static`
- runs: `3`
- total: `216` rows

Current tunables:

```bash
RECURRENCE_TASKS="T04,T11,T13,T14,T15,T20"
RECURRENCE_VARIANTS=3
RECURRENCE_CONDITIONS="full,no_verification,no_optimization,static"
RECURRENCE_RUNS=3
RECURRENCE_CONDITION_BUDGET_HOURS=8
```

Like Stage 1, this stage launches detached and must be rerun after completion for verification and audit closeout.

### Stage 4: longitudinal

```bash
scripts/eval_protocol/stage4_longitudinal.sh "$RUN_DIR"
```

Default longitudinal run:

- `EXPECTED_CYCLES=40`
- one shared warm registry
- one campaign DB
- no inter-task reset

This is the primary protocol stage for `M6`.

After the detached run completes, rerun the same wrapper to:

- snapshot the DB
- run the Stage 4 gate
- generate a sampled-cycle LangFuse audit request

Override example:

```bash
EXPECTED_CYCLES=60 FLOOR_PER_CYCLE=8 scripts/eval_protocol/stage4_longitudinal.sh "$RUN_DIR"
```

### Stage 5: plausibility priors

```bash
scripts/eval_protocol/stage5_plausibility.sh "$RUN_DIR"
```

This stage reads the Stage 1 and Stage 4 snapshots and checks for:

- impossible or suspicious success-rate patterns
- dead tasks
- suspicious capability monoculture
- snapshot leakage signs
- other shape-plausibility anomalies

If there are no `INVESTIGATE` flags, it can pass directly. If flags are raised,
it generates a targeted audit request and waits for verdicts before advancing.

## Audit Checkpoints

Several stages intentionally stop at `SHAPE_OK` until a human trace audit is completed.

The automation does not advance a stage from `SHAPE_OK` to `PASS` by itself.
Advancement requires either:

- a completed `stageN_audit_verdicts.md` file with a clean summary, or
- a manual marker override

To close one:

1. open `stageN_audit_request.md` in the run directory
2. complete the requested review using a reviewer workflow with LangFuse trace access
3. save the response as `stageN_audit_verdicts.md`
4. rerun the same stage wrapper

That is how a stage advances from `SHAPE_OK` to `PASS`.

## Direct Runners (for debugging, not the canonical Chapter 6 path)

The wrappers above are the supported evaluation entrypoint. These scripts are
still useful for debugging or ad-hoc runs:

- [`scripts/run_pilot.py`](scripts/run_pilot.py)
- [`scripts/run_longitudinal.py`](scripts/run_longitudinal.py)
- [`scripts/run_recurrence_eval.py`](scripts/run_recurrence_eval.py)
- [`scripts/analyze_results.py`](scripts/analyze_results.py)
- [`scripts/analyze_longitudinal.py`](scripts/analyze_longitudinal.py)
- [`scripts/analyze_recurrence.py`](scripts/analyze_recurrence.py)

Examples:

```bash
uv run scripts/run_pilot.py --all-tasks --all-conditions --n-runs 3 --db data/eval/metrics_manual.duckdb --fresh-db
uv run scripts/run_longitudinal.py --db data/eval/longitudinal.duckdb --n-tasks 40 --fresh-db
uv run scripts/run_recurrence_eval.py --db data/eval/metrics_recurrence_manual.duckdb --metrics-root data/eval/recurrence_metrics_manual
```

Use these only when you understand how they map back to the protocol.

## Outputs You Should Expect

During a protocol run:

- live DBs and registries are written under [`data/eval/`](data/eval/)
- protocol snapshots, logs, markers, and audit files are written under the run directory in [`docs/eval_runs/`](docs/eval_runs/)

Important captured artifacts include:

- `*.marker`
- `*_gate.json`
- `*_agreement.json`
- `*.duckdb`
- `*_audit_request.md`
- `*_audit_verdicts.md`

The reporting stack is built from those artifacts into:

- [`docs/chapter6_numbers.md`](docs/chapter6_numbers.md)
- [`docs/chapter6_fact_pack.md`](docs/chapter6_fact_pack.md)
- [`docs/chapter6_evaluation_report.md`](docs/chapter6_evaluation_report.md)

## Troubleshooting

### `OPENAI_API_KEY` missing

Set it in `.env` before running any LLM-backed script.

### Docker image missing

Build the sandbox image:

```bash
docker build -t jit-sandbox sandbox
```

Or set `SANDBOX_IMAGE` in `.env` to a different compatible image name.

### Stage blocked by prior marker

The wrappers enforce stage order. If a stage says the prior stage is not `PASS`,
finish that audit / rerun loop first.

### Detached runner still active

Stages 1, 3, and 4 write PID files and logs into the run directory. Re-run the
same wrapper to check status; do not launch a second copy against the same state.

### Linux / non-macOS detached run

The detached wrappers use `caffeinate`. On non-macOS, run the underlying Python
scripts manually or patch the wrappers for your environment.

## Development Notes

- unit tests: `uv run pytest tests/unit/ -q`
- formatting / lint: `uv run ruff check .`

The generated-skill and registry runtime state lives under `data/eval/`, not
under `src/`.

## Summary

If you are new to this repo and want the supported path:

1. install deps with `uv`
2. copy [`.env.example`](.env.example) to `.env`
3. build `jit-sandbox`
4. use [`docs/EVAL_PROTOCOL.md`](docs/EVAL_PROTOCOL.md) plus the wrappers in [`scripts/eval_protocol/`](scripts/eval_protocol/)
5. treat `docs/archive/` as history, not setup documentation

That is the supported setup path for this repository.
