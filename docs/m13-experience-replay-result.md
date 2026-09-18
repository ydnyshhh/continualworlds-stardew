# M13 Experience-Replay Prioritization Result

## Outcome

M13 passed its controlled 40-run gate and authenticated GLM 5.3 validation.

| Condition | Runs | Held-out accuracy | Useful replay precision | Wasted replays |
|---|---:|---:|---:|---:|
| No replay | 8 | 0.0000 | 0.0000 | 0.000 |
| Recency | 8 | 0.0000 | 0.0000 | 3.000 |
| Fixed-seed random | 8 | 0.4583 | 0.4583 | 1.625 |
| Error priority | 8 | 0.6667 | 0.6667 | 1.000 |
| Agent priority | 8 | **1.0000** | **1.0000** | **0.000** |

Agent-prioritized replay improved held-out accuracy over error-priority replay by **0.3333**.
The deterministic paired-bootstrap 95% interval was `[0.3333, 0.3333]`, and the exact two-sided
sign-test result was `p=0.0078125`. All candidate states and hidden evaluations matched within
paired seeds. There were no budget failures, hidden-field leaks, event-chain failures, exclusions,
or manual score edits.

The machine-readable controlled result is
[`runtime/smoke/m13-experience-replay-v1.json`](../runtime/smoke/m13-experience-replay-v1.json).

## Authenticated GLM 5.3 validation

GLM 5.3 selected the crop-deadline, mine-retreat, and gift-preference failures. It rejected the
recent but irrelevant decorative failure and both low-error recent notes.

- held-out score: 3/3;
- error-priority counterfactual: 2/3;
- useful replay precision: 1.0;
- replay budget: 3/3;
- model calls: 1;
- input/output tokens: 19/316;
- reported cost: $0.00169476;
- verified events: 24;
- replay provenance: valid.

The authenticated result is
[`runtime/smoke/m13-live-glm-5.3-v3.json`](../runtime/smoke/m13-live-glm-5.3-v3.json).

One intermediate retry timed out before returning a model response. Its failed event chain remains
preserved; the subsequent bounded retry passed.

## Reproduction

```powershell
prime-stardew-m13-gate `
  --gate-root .\runtime\smoke\m13-reproduction `
  --output .\runtime\smoke\m13-reproduction.json
```

The authenticated command additionally requires the existing Prime Agent executable, Node runtime,
Prime configuration, and session-directory arguments used by the other live milestone gates.
