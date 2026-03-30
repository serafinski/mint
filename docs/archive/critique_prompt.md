# Critique Checkpoint — Iteration 5 Evaluation Design

You are a thesis advisor reviewing the evaluation design for a Master's thesis on just-in-time capability synthesis in multi-agent systems. The evaluation has been run: 260 records (13 tasks × 4 conditions × 5 runs). Your job is to determine whether the data collected is sufficient and sound for a defensible thesis. Be constructive but rigorous.

## Research questions the evaluation must answer

1. How does JIT capability synthesis compare to pre-built static capabilities? (Pillar 1)
2. Does three-tier verification improve generated capability quality? (Pillar 2)
3. Does evolutionary granularity optimization improve registry performance over time? (Pillar 3)

## Experimental conditions

| Condition | Pillar 1 (JIT) | Pillar 2 (Verification) | Pillar 3 (Optimizer) |
|---|---|---|---|
| full | ✓ | ✓ | ✓ |
| no_verification | ✓ | ✗ | ✗ |
| no_optimization | ✓ | ✓ | ✗ |
| static | ✗ | ✗ | ✗ |

## Results summary

| Condition | Success | Gate Pass |
|---|---|---|
| no_optimization | 80.0% | 98.5% |
| no_verification | 76.9% | 100% |
| full | 72.3% | 95.4% |
| static | 38.5% | 38.5% |

## Per-task highlights

- T01-T03 always pass (registry match).
- T06 gate=100% success=0% (semantic gap).
- T09 static=100% JIT≈20% (prompt ambiguity).
- T13 agent=100%.
- Optimizer hurts in single-run evaluation (pruning with no invocation history).

## What is captured per record

run_id, condition, task_id, task_tier, attempt_count, capability_type, verification_tier_reached, gate_passed, output_in_range, token_cost (not populated), attempt_latency_ms, failure_reason, registry_snapshot_id, seed, is_pilot, human_label (not populated), timestamp.

## Validators

Ground-truth checks against pre-computed expected values (exact match, tolerance windows, or threshold-based depending on task). Success = gate_passed AND output_in_range.

## Please critique the following dimensions

### 1. Statistical validity

Are 5 runs per condition sufficient for the claims being made? Which claims need more runs? Are there confounds between conditions (e.g., shared snapshot state, task ordering effects)?

### 2. Condition design

Do the 4 conditions isolate the contribution of each pillar? Is there a missing condition (e.g., verification-only without JIT, or optimizer-only)? Does the no_verification condition also disabling the optimizer confound the comparison?

### 3. Task coverage

13 tasks across 3 artifact types. Are there blind spots in task diversity? Are the "always pass" tasks (T01-T03) inflating success rates? Should they be excluded or reported separately?

### 4. Validator soundness

Are the ground-truth validators testing the right thing? Could a capability pass the validator for the wrong reasons (false positives)? Could a correct capability fail (false negatives, e.g., T09's JSON structure ambiguity)?

### 5. Semantic correctness gap

T06 (gate=100%, success=0%) is the strongest finding about verification limits. Is one task sufficient evidence? Are there other tasks where this gap exists but is masked?

### 6. Optimizer evaluation fairness

The optimizer was designed for longitudinal use but evaluated in single-run snapshots. Is this a flaw in the evaluation or an honest finding? What would a fair optimizer evaluation look like, and is it feasible within thesis scope?

### 7. Missing metrics

token_cost and human_label are empty. How much does this weaken the thesis? What can be recovered from LangFuse traces?

### 8. Reproducibility

Snapshot restore + seed control + DuckDB persistence. Are there hidden sources of non-determinism (LLM temperature, network latency, Docker state)?

### 9. Threats to validity

- **Internal:** Are differences between conditions real or noise?
- **External:** Does the business analytics testbed generalize?
- **Construct:** Does "success" as defined actually measure capability quality?

### 10. Actionable recommendations

What specific changes would strengthen the evaluation before thesis submission? Rank by impact vs. effort.
