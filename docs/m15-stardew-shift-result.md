# M15 Stardew-Shift Result

## Controlled study

The final preregistered study completed all **32 runs**: eight seeds across four matched
conditions. Every event chain verified, all world/observation/start hashes matched within seed,
and no hidden optimal action appeared in a decision input.

| Condition | B adaptation lag | Return retention | Return lag | Relearning ratio | Stale-belief uses | B shifted accuracy | B stable accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| Frozen A | 6.0 | 1.0 | 0.0 | 0.000 | 12.0 | 0.000 | 1.000 |
| Global overwrite | 1.0 | 0.0 | 1.0 | 0.667 | 4.0 | 0.833 | 1.000 |
| Contextual beliefs | **1.0** | **1.0** | **0.0** | **0.000** | **2.0** | **0.833** | **1.000** |
| Fresh at B | 1.5 | 0.0 | 1.0 | 0.667 | 2.0 | 0.833 | 0.833 |

Contextual minus global-overwrite return retention was **+1.0 per seed**. The deterministic
paired-bootstrap 95% interval was **[1.0, 1.0]**, and the exact two-sided sign-test result was
**p = 0.0078125**.

Contextual adaptation reached stable B behavior after one interaction, 0.5 interactions faster
than the fresh-at-B mean. Its first-two-interaction B regret exactly matched the fresh baseline, so
measured negative transfer was zero. It contextualized both changed mechanics while retaining
perfect behavior on the unchanged resource-distribution mechanic.

The conditions reproduce the intended failure modes:

- `frozen_a` preserves A but never adapts either shifted mechanic in B.
- `global_overwrite` adapts quickly in B but uses stale B beliefs on return and must relearn A.
- `contextual_beliefs` pays one contradictory observation in B, keeps the A records intact, and
  recovers A immediately from the returning context cue.

Final artifact:
[`runtime/smoke/m15-stardew-shift-v2.json`](../runtime/smoke/m15-stardew-shift-v2.json).
The v1 artifact is retained as a preliminary implementation run; its surprise rule scheduled an
unnecessary probe after reward increases. The corrected rule and stale-belief instrumentation were
fixed before the final v2 execution. The preregistration did not change.

## Authenticated GLM 5.3 gate

Prime Agent called OpenRouter `z-ai/glm-5.3` twice with minimal reasoning and strict JSON schemas.

- World B next actions correct: **3/3**.
- Shifted mechanics contextualized: **2/2**.
- Stable mechanic retained: **yes**.
- Return-to-A actions correct: **3/3**.
- Global-overwrite return counterfactual: **1/3**.
- Belief provenance valid: **yes**.
- Hidden evaluator fields exposed: **no**.
- Usage: **3,268 input**, **513 output** tokens.
- Cost: **$0.0068324**.
- Event chain: **11 events**, final state `completed`.

Artifact:
[`runtime/smoke/m15-live-glm-5.3-v1.json`](../runtime/smoke/m15-live-glm-5.3-v1.json).

## Reproduction

Controlled study:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m15_gate `
  --gate-root .\runtime\smoke\m15-stardew-shift-reproduction `
  --output .\runtime\smoke\m15-stardew-shift-reproduction.json
```

Authenticated gate:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m15_live `
  --provider openrouter --model z-ai/glm-5.3 `
  --prime-executable .\runtime\prime-agent\prime-agent.cmd `
  --node-dir .\runtime\prime-bootstrap\node-v22.23.2-win-x64 `
  --prime-config-dir .\runtime\prime-config `
  --prime-session-dir .\runtime\prime-sessions\m15-live-reproduction `
  --gate-root .\runtime\smoke\m15-live-reproduction `
  --output .\runtime\smoke\m15-live-reproduction.json
```

## Interpretation and limitations

M15 establishes contextual stability and plasticity for a small deterministic counterfactual
world family. The seeded action mappings prevent success from being explained by memorized
Stardew facts, and the unchanged mechanic detects indiscriminate revision.

The study uses abstract mechanics and deterministic rewards. Visible context cues are reliable,
phase lengths are short, and the authenticated gate covers one seed. These results do not yet show
change-point detection without a cue, adaptation under noisy or gradual drift, or modified rules
inside live Stardew gameplay.

The recommended next milestone is **M16 memory corruption and autonomous repair**: inject plausible
false and stale records without labeling them, then measure detection, correction, recurrence, and
utility loss.
