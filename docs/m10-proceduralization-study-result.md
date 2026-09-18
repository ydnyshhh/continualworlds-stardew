# M10 preregistered proceduralization study

## Result

M10 passed its research gate on 2026-09-17. Eight matched seeds ran for 28 days under each
condition. Adding the validated procedural watering skill reduced model decisions from 28 to
23 in every pair. All three project outcomes and all 592 primitive actions were unchanged.

| Measure | D: memory + retrieval | E: D + skills | Paired effect |
|---|---:|---:|---:|
| Model decisions per season | 28 | 23 | 5 fewer |
| Successful projects per season | 3 | 3 | 0 |
| Mean final project score | 1.0 | 1.0 | 0.0 |
| Primitive actions per season | 592 | 592 | 0 |
| Invalid actions | 0 | 0 | 0 |

The mean decision reduction was 5.0. Its deterministic paired-bootstrap 95% confidence
interval was [5.0, 5.0]. The exact two-sided sign-test p-value was 0.0078125. The preregistered
zero-margin outcome non-inferiority check passed.

## Design and mechanism

The content-hashed preregistration is
[`configs/m10-proceduralization-study.yaml`](../configs/m10-proceduralization-study.yaml), SHA-256
`357e9eec3f8db473aa1a55dabe188625bd0ed149576baac32d850802c1b775b7`.
It fixes the hypothesis, eight seeds, conditions, primary metric, favorable direction, exclusion
rules, significance threshold, confidence interval, and outcome check.

Both members of each pair restore `paired-start-checkpoint.json`, which binds the preregistration
hash to the serialized state and its content hash. The identical starting state hash is
`25f352f9b1a9a1576b77324906edbfc3f30e5ee2023485251b600f1de386acd2`. The sole condition
difference is skill availability. After three successful watering uses, E can invoke the validated
watering procedure on five later routine watering days. The procedure removes a model decision
while retaining the same typed actions and game-state transition.

Each run retains its immutable configuration, code and runtime provenance, preregistration hash,
starting-state hash, and hash-chained append-only event log. The final checkpoint-enforced
aggregate report is at `runtime/smoke/m10-proceduralization-study-v2.json`.

## Failure analysis

The 16 runs produced no exclusions, missing or duplicate pairs, invalid actions, project failures,
start-state mismatches, or nonpositive effects. Each event log contained 110 authenticated events.
The analysis rejects incomplete pairs and fails the gate if the confidence interval crosses zero,
the exact test misses alpha, an outcome is inferior, or any preregistered exclusion occurs.

## Interpretation limit

The treatment assignment isolates procedural-skill availability, so the five-decision reduction
is a causal result for this fixed policy and deterministic replay environment. The test does not
show that the magnitude transfers to free-form model behavior or the live Stardew process. Those
external-validity tests belong in the later lifetime and transfer milestones.

## Reproduce

From the repository root, use a new output directory:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m10_gate `
  --gate-root .\runtime\smoke\m10-reproduction `
  --output .\runtime\smoke\m10-reproduction.json
```

The command executes all 16 season runs, writes individual result records and append-only logs,
runs the preregistered paired analysis, emits failure analysis, and exits unsuccessfully if any
gate condition fails.
