# PrimeStardew

PrimeStardew is a research platform for studying continual learning and agent
self-improvement in Stardew Valley without model weight updates.

The local full game provides the simulator. StarDojo, running as a SMAPI mod,
provides observations and actions. The agent, memory, skill, experiment, and
evaluation layers will live in this repository.

## Current status

All milestones from M0 through M17 are implemented. The current build uses Stardew Valley 1.6.15,
SMAPI 4.5.2, patched StarDojo 1.0.0, Prime Agent 0.9.5, and OpenRouter `z-ai/glm-5.3` for
authenticated model gates.

### Validation snapshot

- **165 automated tests** pass.
- Live validation covers observation, movement, tools, farming, pause/resume, day transitions,
  save/restart, checkpoint recovery, and 50 observation/action cycles.
- Every scored live trajectory uses ordinary game actions and records zero privileged actions.
- Experiment history is stored as hash-chained JSONL events with combined game, agent,
  configuration, memory, named learning-database, and event-cursor checkpoints.

### E1 long-horizon study

The [E1 design](docs/E1_LONG_HORIZON_DESIGN.md) defines the six-condition continual-learning
ablation, paired-seed protocol, disposable probes, normalized probe AULC, recurring competency
metrics, inference accounting, and Spring Year 2 reset forks. The current deterministic contract
gate covers 12 synthetic condition/seed runs and deliberately gives every condition the same probe
curve. It validates the machinery and does not constitute an E1-Pilot result.

Run the contract gate with:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.e1_offline_gate `
  --config .\configs\e1-offline-validation.yaml `
  --root .\runtime\smoke\e1-offline-contract `
  --output .\runtime\smoke\e1-offline-contract.json
```

### Milestones

| Milestone | Capability | Result |
|---|---|---|
| M0 | Research scope and Windows setup | Full game, SMAPI, and patched StarDojo environment established. |
| [M1](docs/control-test-result.md) | Typed game controls | Movement, tools, farming, lifecycle controls, and occupancy guards passed live validation. |
| [M2](docs/recovery-test-result.md) | Checkpoints and recovery | Save verification, tamper detection, restore, and forced-disconnect recovery passed. |
| [M3](docs/m3-task-harness-result.md) | Atomic task harness | Five task types scored 1.0 across three clean repetitions each. |
| [M4](docs/m4-experiment-runner-result.md) | Reproducible experiments | Immutable configs, stable run IDs, event chains, budgets, and checkpoint-backed resume implemented. |
| [M5](docs/m5-prime-integration-result.md) | Prime Agent integration | GLM 5.3 completed all five live tasks with attributable calls and strict typed decisions. |
| [M6](docs/m6-memory-result.md) | Persistent memory | Only memory plus retrieval recovered the hidden fact in the authenticated ablation. |
| [M7](docs/m7-reflection-result.md) | Reflection and belief revision | Formed and revised an evidence-linked hidden crop rule while excluding superseded beliefs. |
| [M8](docs/m8-skills-result.md) | Procedural skills | Watering skill scored 1.0 in replay and live tests and saved two model decisions. |
| [M9](docs/m9-season-benchmark-result.md) | Season benchmark | Three seeds completed all projects and 28 days; interrupted execution resumed exactly once. |
| [M10](docs/m10-proceduralization-study-result.md) | Causal skill study | Skills reduced decisions from 28 to 23 while preserving outcomes and 592 primitive actions. |
| [M11](docs/m11-lifetime-transfer-result.md) | Lifetime transfer | Full inheritance raised held-out success from 0.5 to 1.0 and cut decisions from 8 to 4. |
| [M12](docs/m12-memory-management-result.md) | Selective memory | Agent selection doubled retained utility and improved utility per token over FIFO. |
| [M13](docs/m13-experience-replay-result.md) | Experience replay | Agent priority scored 3/3 versus error priority's 2/3 under the same replay budget. |
| [M14](docs/m14-active-experimentation-result.md) | Active experimentation | Controlled reward rose from 1,680 to 2,280; live held-out twig yield was predicted exactly. |
| [M15](docs/m15-stardew-shift-result.md) | `A -> B -> A` adaptation | Contextual beliefs adapted to B in one interaction and recovered A with zero lag. |
| [M16](docs/m16-memory-corruption-result.md) | Autonomous memory repair | Adaptive repair reached 7/7 held-out accuracy with no clean false repairs. |
| [M17](docs/m17-noisy-curriculum-result.md) | Noisy and adversarial learning | Agent curriculum reached 8/8 hidden accuracy and rejected a spoofed live observation. |

## Documents

- [Implementation plan](docs/implementation-plan.md)
- [Implementation progress](docs/progress.md)
- [Windows setup and validation](docs/windows-setup.md)
- [Events and run checkpoints](docs/events-and-run-checkpoints.md)
- [Live movement, tool, and farming controls](docs/control-test-result.md)
- [Live interruption and exact-once recovery](docs/recovery-test-result.md)
- [Deterministic atomic-task gate](docs/m3-task-harness-result.md)
- [Experiment runner and resume gate](docs/m4-experiment-runner-result.md)
- [Prime RPC integration and contract gate](docs/m5-prime-integration-result.md)
- [Persistent memory and retrieval ablation](docs/m6-memory-result.md)
- [Reflection, belief revision, and consolidation](docs/m7-reflection-result.md)
- [Procedural skills and causal ablation](docs/m8-skills-result.md)
- [Project and 28-day season benchmark](docs/m9-season-benchmark-result.md)
- [Preregistered proceduralization study](docs/m10-proceduralization-study-result.md)
- [Lifetime, transfer, and routing gate](docs/m11-lifetime-transfer-result.md)
- [Selective memory-management result](docs/m12-memory-management-result.md)
- [Experience-replay prioritization design](docs/m13-experience-replay-design.md)
- [Experience-replay prioritization result](docs/m13-experience-replay-result.md)
- [Active experimentation and disposable live-game gate](docs/m14-active-experimentation-result.md)
- [Stardew-Shift design](docs/m15-stardew-shift-design.md)
- [Memory-corruption design](docs/m16-memory-corruption-design.md)
- [Memory-corruption result](docs/m16-memory-corruption-result.md)
- [Noisy curriculum design](docs/m17-noisy-curriculum-design.md)
- [Noisy curriculum and live corruption result](docs/m17-noisy-curriculum-result.md)
- [E1 long-horizon continual-learning study design](docs/E1_LONG_HORIZON_DESIGN.md)
- [Stardew-Shift results](docs/m15-stardew-shift-result.md)
- [Machine-readable smoke result](docs/smoke-test-result.json)
- [StarDojo compatibility patch](patches/stardojo-windows-build.patch)

## Diagnostic launch

Create the locked Python environment. Copy mode avoids OneDrive reparse-point
permission failures on this workspace:

```powershell
uv sync --extra dev --link-mode copy
```

```powershell
.\scripts\start-stardojo.ps1
```

In a second terminal:

```powershell
.\.venv\Scripts\prime-stardew-command.exe `
  'observe_v2%1' --output .\runtime\observation.json
```

Runtime downloads, saves, observations, checkpoints, and the upstream checkout are
ignored by Git.

## Combined checkpoint commands

Create agent and configuration JSON files, start an `EventStore` for the run, then
create a bundle with:

```powershell
.\.venv\Scripts\prime-stardew-run-checkpoint.exe create `
  --run-id my-run --event-log .\runtime\runs\my-run\events.jsonl `
  --save-id PrimeStardewSmoke_406041616 --player PrimeStardewSmoke `
  --year 1 --season spring --day 6 `
  --agent-state .\agent-state.json --configuration .\run-config.json `
  --destination .\runtime\checkpoints\my-run-spring06
```

The `verify` and `restore` subcommands can authenticate the saved cursor against the
same retained event log. See the detailed format and recovery sequence in the linked
document above.

Preview checkpoint retention without deleting anything:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.retention_cli `
  --root .\runtime\checkpoints --latest-days 2
```

Base, milestone, failure, malformed, and hash-invalid bundles are protected. Passing
`--apply` recomputes and re-verifies the displayed plan before pruning ordinary day
bundles.

## Experiment commands

Validate the checked-in M4 configuration without creating a run:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.experiment_cli `
  --config .\configs\m4-scripted.yaml validate
```

Use `init`, `start`, `status`, or `transition` with the same command to manage a
durable run. Run IDs include the suite, model, condition, seed, and the first 12
hexadecimal characters of the canonical configuration hash.

Run the offline M5 contract gate against the live-recorded M3 trajectories:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m5_gate `
  --m3-report .\runtime\smoke\m3-atomic-3x.json `
  --gate-root .\runtime\smoke\m5-contract-gate `
  --output .\runtime\smoke\m5-gate.json
```

After authenticating a provider in the local Prime config and starting StarDojo, run
the live gate against the disposable Spring 8 fixture with:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m5_live `
  --provider openrouter --model z-ai/glm-5.3 `
  --prime-executable .\runtime\prime-agent\prime-agent.cmd `
  --node-dir .\runtime\prime-bootstrap\node-v22.23.2-win-x64 `
  --prime-config-dir .\runtime\prime-config `
  --prime-session-dir .\runtime\prime-sessions\m5-live `
  --save-id PrimeStardewM3A_406041616 --player PrimeStardewSmoke `
  --repetition 1 `
  --runs-root .\runtime\runs `
  --output .\runtime\smoke\m5-live.json
```

Run the deterministic M6 causal contract:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m6_gate `
  --gate-root .\runtime\smoke\m6-memory-gate `
  --output .\runtime\smoke\m6-memory-gate.json
```

Run the same paired ablation through authenticated Prime Agent sessions:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m6_live `
  --provider openrouter --model z-ai/glm-5.3 `
  --prime-executable .\runtime\prime-agent\prime-agent.cmd `
  --node-dir .\runtime\prime-bootstrap\node-v22.23.2-win-x64 `
  --prime-config-dir .\runtime\prime-config `
  --prime-session-root .\runtime\prime-sessions\m6-live `
  --gate-root .\runtime\smoke\m6-live `
  --output .\runtime\smoke\m6-live.json
```

Use `prime_stardew.memory_cli` to add, list, retrieve, export, or import memories.
Combined run checkpoints accept `--memory-database` on creation and
`--destination-memory-path` on restore.

Run the deterministic M7 reflection and belief-revision gate:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m7_gate `
  --gate-root .\runtime\smoke\m7-reflection-gate-v1 `
  --output .\runtime\smoke\m7-reflection-gate.json
```

Run the same two-stage revision through an authenticated Prime Agent process:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m7_live `
  --provider openrouter --model z-ai/glm-5.3 `
  --prime-executable .\runtime\prime-agent\prime-agent.cmd `
  --node-dir .\runtime\prime-bootstrap\node-v22.23.2-win-x64 `
  --prime-config-dir .\runtime\prime-config `
  --prime-session-dir .\runtime\prime-sessions\m7-live `
  --gate-root .\runtime\smoke\m7-live `
  --output .\runtime\smoke\m7-live.json
```

Run the deterministic M8 skill ablation over the three proven M3 watering traces:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m8_gate `
  --m3-report .\runtime\smoke\m3-atomic-3x.json `
  --gate-root .\runtime\smoke\m8-skills-gate `
  --output .\runtime\smoke\m8-skills-gate.json
```

With the disposable Stardew fixture running, authenticate the proposal and validate the
skill in the live game:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m8_live `
  --m3-report .\runtime\smoke\m3-atomic-3x.json `
  --provider openrouter --model z-ai/glm-5.3 `
  --prime-executable .\runtime\prime-agent\prime-agent.cmd `
  --node-dir .\runtime\prime-bootstrap\node-v22.23.2-win-x64 `
  --prime-config-dir .\runtime\prime-config `
  --prime-session-dir .\runtime\prime-sessions\m8-live `
  --gate-root .\runtime\smoke\m8-live `
  --output .\runtime\smoke\m8-live.json
```

Run the three-seed M9 season gate with an injected Day 14 interruption:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m9_gate `
  --gate-root .\runtime\smoke\m9-season-gate `
  --output .\runtime\smoke\m9-season-gate.json
```

Rebuild one run report solely from its retained append-only event log:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m9_report `
  --event-log .\runtime\smoke\m9-season-gate\runs\RUN_ID\events.jsonl `
  --run-id RUN_ID --seed 406041616 --condition full-harness `
  --output .\runtime\smoke\m9-rebuilt.json
```

Reproduce the complete preregistered M10 study and statistical analysis in one command:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m10_gate `
  --gate-root .\runtime\smoke\m10-reproduction `
  --output .\runtime\smoke\m10-reproduction.json
```

Run the configured M11 full-year retention, held-out transfer, and routing gate:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m11_gate `
  --gate-root .\runtime\smoke\m11-reproduction `
  --output .\runtime\smoke\m11-reproduction.json
```

With StarDojo running on a disposable Spring 8 clone, reproduce the authenticated GLM 5.3 and
live season-boundary gate:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m11_live `
  --prime-executable .\runtime\prime-agent\prime-agent.cmd `
  --node-dir .\runtime\prime-bootstrap\node-v22.23.2-win-x64 `
  --prime-config-dir .\runtime\prime-config `
  --prime-session-dir .\runtime\prime-sessions\m11-live-reproduction `
  --save-id PrimeStardewM11Boundary_406041616 `
  --gate-root .\runtime\smoke\m11-live-reproduction `
  --output .\runtime\smoke\m11-live-reproduction.json
```

Run the preregistered 40-run M12 bounded-memory study:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m12_gate `
  --gate-root .\runtime\smoke\m12-memory-study-reproduction `
  --output .\runtime\smoke\m12-memory-study-reproduction.json
```

Run the authenticated M12 memory-management decision through Prime Agent and GLM 5.3:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m12_live `
  --provider openrouter --model z-ai/glm-5.3 `
  --prime-executable .\runtime\prime-agent\prime-agent.cmd `
  --node-dir .\runtime\prime-bootstrap\node-v22.23.2-win-x64 `
  --prime-config-dir .\runtime\prime-config `
  --prime-session-dir .\runtime\prime-sessions\m12-live-reproduction `
  --gate-root .\runtime\smoke\m12-live-reproduction `
  --output .\runtime\smoke\m12-live-reproduction.json
```

M12 preserves historical records while bounding the active set by an explicit token estimate.
See [`docs/m12-memory-management-result.md`](docs/m12-memory-management-result.md) for the design,
controlled results, authenticated result, and limitations.

Run the preregistered 40-run M14 hidden-mechanics study:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m14_gate `
  --gate-root .\runtime\smoke\m14-active-experimentation-reproduction `
  --output .\runtime\smoke\m14-active-experimentation-reproduction.json
```

Run the authenticated active-experiment selection through Prime Agent and GLM 5.3:

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

See [`docs/m14-active-experimentation-result.md`](docs/m14-active-experimentation-result.md) for
the causal design, results, provider-attempt history, and limitations.
