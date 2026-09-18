# PrimeStardew

PrimeStardew is a research platform for studying continual learning and agent
self-improvement in Stardew Valley without model weight updates.

The local full game provides the simulator. StarDojo, running as a SMAPI mod,
provides observations and actions. The agent, memory, skill, experiment, and
evaluation layers will live in this repository.

## Current status

The Windows environment proof is complete on Stardew Valley 1.6.15, SMAPI 4.5.2,
and a patched StarDojo 1.0.0 build. The validated path includes structured
observation, an action with observed state change, pause/resume, day transition,
save creation, restart, and checkpoint reload using a disposable test farmer.

Milestones M1 through M17 have controlled gates. The typed Python SDK passed 154 automated tests, a live
reversible smoke test, a forced-disconnect recovery test, and 50 live
observation/action cycles. M2 checkpoint creation, verification, tamper detection,
and restore under a new experiment save ID are implemented and live-validated.
Experiments now have hash-chained append-only JSONL events and atomic combined
checkpoints for game, agent, configuration, and event-cursor state.
Autonomous movement now uses a destination occupancy guard with verified movement
postconditions.
The deterministic task harness scored turn/move, water crop, clear debris,
harvest crop, and chest transfer at 1.0 on three clean repetitions each, with no
privileged command inside any scored trajectory.
M4 adds immutable YAML run configuration, stable content-addressed run IDs,
provenance, lifecycle enforcement, budget accounting, idempotent event recording,
normalized trajectory comparison, and checkpoint-backed resume.
M5 adds a persistent Prime Agent JSONL RPC adapter, provider-neutral request and
response schemas, deterministic bounded context, strict allowlisted JSON decisions,
one repair attempt, actual provider/model/API attribution, paused-game inference,
and checkpointed goals without transcript replay. Prime Agent 0.9.5 and a private
Node 22.23.2 runtime are installed under `runtime`; a no-model RPC `get_state` probe
passed. The offline gate passed all five atomic trajectories. The authenticated live
gate also passed all five tasks through OpenRouter `z-ai/glm-5.3`: six attributable
model calls, five 1.0 task scores, and zero privileged actions in scored trajectories.
M6 adds an external SQLite memory store with immutable episode, semantic, and belief
records; append-only lifecycle events; deterministic keyword and metadata retrieval;
validity and confidence filters; ranked retrieval telemetry; hashed transfer bundles;
and memory-aware combined checkpoints. Its paired authenticated ablation used fresh
Prime sessions with OpenRouter `z-ai/glm-5.3`. Only memory plus retrieval recovered the
hidden fact, and deleting the memory removed the gain without changing game state.
M7 adds fixed-budget evidence selection, structured reflection, citation-checked
versioned beliefs, contradiction-driven revision, episode-to-semantic consolidation,
four refinement policies, and prediction calibration. Both the deterministic gate and
an authenticated two-call OpenRouter `z-ai/glm-5.3` gate formed and revised the hidden
crop rule while excluding superseded and deleted beliefs from current retrieval.
M8 adds immutable parameterized skill versions, model-driven proposals from successful
trajectories, a restricted typed-action compiler, replay and disposable-live validation,
append-only activation and rollback, and per-use reasoning/action savings. Its watering
skill passed three deterministic and three live executions at score 1.0. After charging
the one-call proposal, the skill condition saved two model decisions while preserving the
same nine primitive actions and task outcome.
M9 adds three versioned project benchmarks, daily intermediate scoring, a deterministic
28-day Spring environment, authenticated replay checkpoints, injected-crash recovery,
multi-seed aggregation, and report reconstruction exclusively from raw events. Three seeds
completed all 28 days and all three projects; the interrupted seed resumed from Day 14
without a duplicate day completion. These are benchmark-system results, not causal claims.
M10 adds immutable study preregistration, paired condition/seed execution, authenticated
start-state identity, deterministic bootstrap confidence intervals, an exact sign test, and
machine-readable failure analysis. Across eight paired 28-day runs per condition, procedural
skills reduced model decisions from 28 to 23 while preserving all project outcomes and 592
primitive actions. The causal claim is limited to the fixed deterministic replay environment.
M11 adds a configured 112-day four-season lifetime, authenticated season-boundary checkpoints,
an 84-day dormant-skill retest, independent hash-verified memory and skill transfer bundles,
held-out contamination checks, five recipient baselines, and fixed-budget model-tier routing.
Full inheritance raised held-out success from 0.5 to 1.0 and reduced decisions from 8 to 4.
The external-validity gate then moved the same semantic state into authenticated OpenRouter
`z-ai/glm-5.3`: fresh sessions scored 0/2 and transferred sessions scored 2/2. A transferred
M8 watering skill also scored 1.0 on a disposable live save after 20 verified transitions to
Spring 28, with zero privileged actions in the scored trajectory.
M12 adds bounded selective memory management with append-only status transitions and authenticated
agent selection. M13 adds fixed-budget experience-replay prioritization with exact replay-event
provenance and held-out answer isolation. Across 40 matched M13 runs, agent priority reached 1.0
held-out accuracy versus 0.6667 for error priority. Authenticated GLM 5.3 selected all three
transferable failures in one call, scored 3/3 versus 2/3, and produced a verified 24-event chain.
M14 adds costly active experimentation, immutable evidence-linked belief revision, matched causal
conditions, and a preregistered eight-seed study. Its controlled agent condition improved reward
from 1,680 to 2,280. A separate authenticated live Stardew gate restored two identical disposable
Spring 8 saves: GLM 5.3 chose two twig samples, observed 1 and 1 Wood, predicted the held-out yield
as 1, and observed 1 in both the active and matched-control branches. The live scored phase used
zero privileged actions and completed a verified 34-event chain.
M15 adds seeded, versioned counterfactual worlds and an `A -> B -> A` stability-plasticity study.
Across 32 matched runs, contextual beliefs adapted to B in one interaction and recovered A with
zero lag; a global belief adapted equally quickly but required relearning when A returned. The
paired return-retention effect was +1.0 with 95% CI [1.0, 1.0] and p=0.0078125. An authenticated
two-call GLM 5.3 gate contextualized both shifted mechanics, retained the stable mechanic, and
selected all three correct actions when A returned.
M16 adds five hidden memory-corruption types, clean decoys, immutable evidence-linked repair,
restricted schema-level policy patches, and a two-episode causal study. Across 32 matched runs,
adaptive repair reduced evidence required from two contradictory observations to one and improved
held-out utility retention by 15.625 percentage points (95% CI [15.0, 16.25], p=0.0078125), with
zero clean false repairs or post-correction recurrence. An authenticated GLM 5.3 gate improved from
6/7 before its self-proposed policy patch to 7/7 afterward, correcting five corrupted records while
preserving two clean records.
M17 adds fixed-budget self-generated curricula for independent noise, burst noise, duplicate-source
attacks, and source spoofing. Across 32 matched runs, agent-generated curricula improved hidden
accuracy over a fixed human curriculum by 28.125 points (95% CI [25.0, 34.375], p=0.0078125) and
matched the scripted weakness-targeted baseline. GLM 5.3 selected the two failed capabilities and
scored 8/8 on hidden evaluation. In a disposable live Stardew save, it superseded a false 5-Wood
twig belief from two live 1-Wood samples, rejected an unauthenticated false claim, and predicted a
third 1-Wood yield exactly using zero privileged scored actions.

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
