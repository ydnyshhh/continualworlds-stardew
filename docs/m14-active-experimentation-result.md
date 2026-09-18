# M14 Active Experimentation and Scientific Learning Result

## Scope

M14 tests whether an agent will spend immediate resources to learn a hidden mechanic when the
expected future benefit justifies the experiment, and whether it will abstain when testing is not
worthwhile. The main study is controlled deterministic simulation/replay. It is not live Stardew
gameplay and does not modify model weights.

The repository audit and design are in
[`m14-active-experimentation-design.md`](m14-active-experimentation-design.md).

## Implementation

- `ExperimentScenario` exposes the safe value, possible candidate values, prior probability,
  production horizon, and test overhead.
- `HiddenMechanicScenario` adds the evaluator-only realized candidate value. Conversion to the
  observable schema removes it before any policy or provider decision.
- `ExperimentDecision` requires exactly one typed experiment/exploit choice per scenario.
- Tests consume the current production cycle, pay explicit overhead, reveal the mechanic, and
  allow the better treatment on later cycles.
- Initial and revised hypotheses use the existing immutable `MemoryKind.BELIEF` records. Revised
  records supersede the prior and cite the exact `experiment_execution` event.
- Reports reopen and verify every hash-chained event log, then recompute rewards, information,
  belief accuracy, restraint, and failure analysis from scenario events.

## Preregistered study

The preregistration was written before study outcome execution at
[`configs/m14-active-experimentation-study.yaml`](../configs/m14-active-experimentation-study.yaml).
It fixes eight seeds, five matched conditions, three scenarios per run, a three-experiment budget,
the primary metric, stopping rule, exclusions, paired bootstrap, and exact sign test.

Each seed contains:

- two six-cycle uncertain mechanics with expected experiment value +300 each;
- one two-cycle costly mechanic with expected experiment value -120;
- one high and one low realized long-horizon value, with their order changed by seed;
- a seeded high or low result for the costly scenario.

The policy cannot infer realized values from scenario IDs because their assignment changes across
seeds. Every condition within a seed has the same hidden-world and observable-state hashes.

## Controlled results

All 40 runs completed.

| Condition | Runs | Mean reward | Experiments | Overhead | Information bits | Belief accuracy | Future reward attributed |
|---|---:|---:|---:|---:|---:|---:|---:|
| No explicit uncertainty | 8 | 1,680.0 | 0.00 | 0.0 | 0.000 | 0.5833 | 0.0 |
| Belief tracking only | 8 | 1,680.0 | 0.00 | 0.0 | 0.000 | 0.5833 | 0.0 |
| Agent experimentation | 8 | **2,280.0** | 2.00 | 40.0 | 2.000 | **0.9167** | **600.0** |
| Oracle experimentation | 8 | 2,420.0 | 1.25 | 40.0 | 1.136 | 0.6667 | 630.0 |
| Fixed-seed random | 8 | 1,847.5 | 1.50 | 52.5 | 1.329 | 0.8750 | 240.0 |

Agent experimentation minus belief tracking reward was **+600 per seed**. The deterministic
paired-bootstrap 95% interval was **[600, 600]**, and the exact two-sided sign-test result was
**p = 0.0078125**.

The agent policy executed every positive-expected-value experiment and zero negative-value
experiments. Fixed-seed random testing entered three negative-value cases. No run was missing or
excluded, hidden fields were never marked exposed, paired hashes all matched, and the report has
zero manual score edits.

Artifact: [`runtime/smoke/m14-active-experimentation-v1.json`](../runtime/smoke/m14-active-experimentation-v1.json).

## Authenticated GLM 5.3 gate

The successful gate used Prime Agent RPC with OpenRouter `z-ai/glm-5.3`. Two bounded calls covered
the two long-horizon scenarios and the short costly scenario, then combined into one globally
validated decision.

- Both +300 expected-value scenarios: `experiment_candidate`.
- The -120 expected-value scenario: `exploit_safe`.
- Agent reward: 2,280.
- Belief-only counterfactual reward: 1,680.
- Reward gain: 600.
- Information gain: 2 bits.
- Evidence-linked belief revisions: 2.
- Negative-value cases restrained: 1/1.
- Model calls: 2.
- Usage: 1,614 input and 680 output tokens.
- Cost: $0.0054756.
- Reconstructed event count: 22.

Artifact: [`runtime/smoke/m14-live-glm-5.3-v5.json`](../runtime/smoke/m14-live-glm-5.3-v5.json).

Authenticated attempts v1-v4 are retained as infrastructure and prompt failures. Each timed out
before an agent decision, experiment, or score. A minimal GLM 5.3 health probe then succeeded in
3.5 seconds, showing the route was available. The successful gate replaced the large generated
schema presentation with a compact shape and split the same observable decision set into two
calls. Strict Pydantic validation, hidden-state isolation, the global budget, and scoring remained
unchanged. The shared Prime adapter was also fixed to drain child stderr continuously, preventing
OS pipe backpressure.

## Disposable live Stardew gate

A separate external-validity gate ran the sampling loop inside Stardew Valley 1.6.15 through
StarDojo. Two hash-identical Spring 8 saves were restored from checkpoint
`86efa2c1-8bce-4f88-8231-00f3c12361ea`. Fixture setup placed three twigs before scoring. The
active branch could sample the first one or two and reserved the third for validation; the matched
control branch cleared only the third. The player's personal save was not loaded or modified.

GLM 5.3 received no numeric yield prior, derived expected-value feature, hidden object ID, or
evaluator outcome. It selected two samples. Live results were:

- Sample yields: 1 and 1 Wood.
- Revised prediction for the held-out twig: 1 Wood.
- Active held-out yield: 1 Wood; absolute prediction error: 0.
- Matched-control held-out yield: 1 Wood.
- Active total: 3 Wood for 6 stamina; control total: 1 Wood for 2 stamina.
- Active/control primitive actions: 15/7, each within the 20-action branch budget.
- Privileged commands during scoring: 0.
- Belief provenance: valid; the immutable revision cites both exact experiment events.
- Authenticated calls: 1 through `openai-completions`, 140 input tokens, 153 output tokens,
  $0.00099464.
- Hash-chained events: 34; final lifecycle state: `completed`.

Artifact: [`runtime/smoke/m14-stardew-live-v3.json`](../runtime/smoke/m14-stardew-live-v3.json).

Attempts v1 and v2 are retained as provider-timeout evidence. Both stopped before any scored
primitive action. A minimal authenticated health check showed that GLM 5.3 was responsive when
reasoning was explicitly bounded, so v3 pinned this compact JSON decision to minimal reasoning.

## Reproduction

Controlled study:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m14_gate `
  --gate-root .\runtime\smoke\m14-active-experimentation-reproduction `
  --output .\runtime\smoke\m14-active-experimentation-reproduction.json
```

Authenticated gate:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m14_live `
  --provider openrouter --model z-ai/glm-5.3 `
  --prime-executable .\runtime\prime-agent\prime-agent.cmd `
  --node-dir .\runtime\prime-bootstrap\node-v22.23.2-win-x64 `
  --prime-config-dir .\runtime\prime-config `
  --prime-session-dir .\runtime\prime-sessions\m14-live-reproduction `
  --gate-root .\runtime\smoke\m14-live-reproduction `
  --output .\runtime\smoke\m14-live-reproduction.json
```

Disposable live-game gate, with SMAPI/StarDojo already listening on port 10783:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m14_stardew_live `
  --source-checkpoint .\runtime\checkpoints\m11-live-source-spring08 `
  --saves-root "$env:APPDATA\StardewValley\Saves" `
  --active-save-id PrimeStardewM14ActiveReproduction_406041616 `
  --control-save-id PrimeStardewM14ControlReproduction_406041616 `
  --prime-executable .\runtime\prime-agent\prime-agent.cmd `
  --node-dir .\runtime\prime-bootstrap\node-v22.23.2-win-x64 `
  --prime-config-dir .\runtime\prime-config `
  --prime-session-dir .\runtime\prime-sessions\m14-stardew-live-reproduction `
  --gate-root .\runtime\smoke\m14-stardew-live-reproduction `
  --output .\runtime\smoke\m14-stardew-live-reproduction.json
```

## Interpretation and limitations

- The controlled result establishes the mechanism in a small synthetic crop-treatment benchmark.
  It does not establish broad scientific reasoning or live-game exploration.
- Visible priors and possible values are supplied. The policy decides whether information is worth
  buying; it does not infer the prior distribution from experience.
- The controlled agent policy uses deterministic expected value. The authenticated model receives
  the same derived expected value as an observable feature after larger arithmetic-heavy prompts
  repeatedly timed out.
- The two long-horizon scenarios deliberately balance one high and one low realization, producing
  a stable +600 paired effect. This is strong mechanism evidence but limited outcome diversity.
- The disposable live gate exercises live actions and evidence-linked belief revision, but all
  three observed yields were 1 Wood. It therefore validates the live execution and prediction
  path without establishing that this fixture is noisy or that the agent adapts under diverse
  noisy outcomes.
- The oracle uses realized hidden values and is only an evaluator upper bound. Its decisions must
  not be interpreted as an implementable agent policy.

M14 supports the bounded statement that, under matched resource budgets and hidden outcomes, the
agent selected costly information-gathering actions when their expected future value was positive,
declined a negative-value experiment, revised beliefs with exact provenance, and improved later
reward over belief tracking without experimentation.

M15 Stardew-Shift is the recommended next milestone. It should test whether these belief and
experimentation mechanisms adapt to changed mechanics and later recover the original context.
