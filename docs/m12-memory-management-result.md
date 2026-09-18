# M12 Self-Improving Memory Management Result

## Scope

M12 adds bounded active memory and explicit retention decisions to the existing immutable
memory system. It does not change foundation-model weights, delete historical records, or claim
live Stardew validation. The primary experiment is a controlled synthetic replay study; one
authenticated GLM 5.3 call validates the provider path and structured decision contract.

The design and repository audit are in
[`m12-memory-management-design.md`](m12-memory-management-design.md).

## Architecture

- `RunConfig.memory_management` freezes the budget type, token limit, policy, random seed, and
  deterministic overflow fallback into the run identity.
- `MemoryBudgetManager` evaluates active records, applies FIFO, LRU, least-retrieved,
  fixed-seed random, no-deletion, or agent-selected policy, and records every deactivation as
  append-only status history.
- `AgentMemoryManagementPolicy` uses the existing `ExperimentRunner` and provider-neutral RPC
  layer. Its strict schema requires exactly one retain/deactivate action for every active ID.
  It permits one existing-style repair call.
- Agent output with missing, duplicate, unknown, inactive, or over-budget selections is rejected.
  A configured deterministic fallback is explicit in both events and the budget report.
- Retrieval access is recorded in SQLite, enabling deterministic LRU and least-retrieved policy.
- Memory export schema v2 authenticates and transfers complete status history. Schema v1 remains
  import-compatible.
- Consolidation nominations cite active source IDs and are logged. The existing
  `MemoryConsolidator` remains the only executor and continues to preserve all source event IDs.

## Events

M12 adds the following typed event names to the existing hash-chained event store:

- `memory_budget_evaluated`
- `memory_management_requested`
- `memory_management_decided`
- `memory_status_changed`
- `memory_consolidation_selected`

The study also records `memory_study_started`, `memory_observed`, `memory_study_scored`, and the
authenticated `m12_authenticated_scored` record so reports can be rebuilt without manual fields.

## Preregistered controlled study

The preregistration was written before outcome execution at
[`configs/m12-memory-study.yaml`](../configs/m12-memory-study.yaml). It fixes eight matched seeds,
five conditions, an 18-token active-memory budget, exclusions, direction, alpha, a 10,000-sample
paired bootstrap, and an exact paired sign test.

Each seed starts with the same six-record state within every condition:

- two early memories useful on later held-out tasks;
- one stale rule;
- three recent distractors;
- insufficient capacity to retain everything.

Conditions are FIFO, least recently used, least retrieved, agent selected, and unbounded. The
first four receive the same 18-token budget. The primary metric is held-out task utility per
active memory token.

### Results

| Condition | Runs | Utility | Active tokens | Utility/token | Precision | Unused fraction |
|---|---:|---:|---:|---:|---:|---:|
| FIFO | 8 | 1.0 | 18 | 0.05556 | 0.50 | 0.50 |
| LRU | 8 | 1.0 | 18 | 0.05556 | 0.50 | 0.50 |
| Least retrieved | 8 | 1.0 | 18 | 0.05556 | 0.50 | 0.50 |
| Agent selected | 8 | 2.0 | 17 | 0.11765 | 1.00 | 0.00 |
| Unbounded | 8 | 2.0 | 58 | 0.03448 | 1.00 | 0.67 |

Agent-selected minus FIFO utility density was **0.06209**. The deterministic paired-bootstrap
95% interval was **[0.06209, 0.06209]**, and the exact two-sided sign-test result was
**p = 0.0078125**. All 40 runs completed. Candidate-state hashes matched within every seed, all
bounded conditions stayed within 18 tokens, no fallback ran, no run was excluded, and the report
contains zero manual score edits.

Artifact: [`runtime/smoke/m12-memory-study-v1.json`](../runtime/smoke/m12-memory-study-v1.json).

## Authenticated GLM 5.3 gate

The successful gate used Prime Agent RPC with OpenRouter `z-ai/glm-5.3`. One attributed model
call returned a valid decision without repair or fallback. It retained the two future-useful
rules, deactivated the stale rule and three distractors, and used 17 of 18 active tokens.

| Policy | Held-out utility | Active tokens | Utility/token |
|---|---:|---:|---:|
| GLM 5.3 agent selected | 2/2 | 17 | 0.11765 |
| FIFO counterfactual | 1/2 | 18 | 0.05556 |

The gate reconstructed its result from 18 authenticated events. It logged 368 output tokens and
$0.0018985. The provider reported one input token, which is implausible given the request size;
the raw reported value is retained and treated as a provider-usage limitation.

The first authenticated artifact is intentionally retained as a failed gate. Its model decision,
budget, and score were correct, but the local validator incorrectly expected the downstream route
label to be `prime-agent-rpc`. Prime Agent accurately reported `openai-completions`. The validator
was corrected to require nonempty actual route attribution, and v2 passed.

Artifacts:

- [`runtime/smoke/m12-live-glm-5.3-v1.json`](../runtime/smoke/m12-live-glm-5.3-v1.json)
- [`runtime/smoke/m12-live-glm-5.3-v2.json`](../runtime/smoke/m12-live-glm-5.3-v2.json)

## Reproduction

Controlled study:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m12_gate `
  --gate-root .\runtime\smoke\m12-memory-study-reproduction `
  --output .\runtime\smoke\m12-memory-study-reproduction.json
```

Authenticated gate:

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

## Limits and interpretation

- The positive causal result is limited to a deliberately small deterministic benchmark. Its
  repeated seeds vary distractor text but do not create independent effect magnitudes.
- The authenticated result is a single provider call on the same synthetic task. It validates
  the mechanism and external model path, not broad generalization or live-game behavior.
- Agent-selected consolidation is schema-valid and auditable, while execution remains delegated
  to the existing provenance-preserving consolidator. This study evaluated retention/deactivation.
- Deactivated records remain immutable and transferable. Reactivation and confidence-changing
  operations are outside this initial M12 scope.
- LRU and least-retrieved coincide with FIFO in this benchmark because the controlled pretest
  access pattern gives them the same final retained set. Future studies should diversify access
  histories before drawing comparative conclusions among naive policies.

M12 supports the bounded claim that selective agent retention improved future utility per active
token over FIFO, LRU, least-retrieved, and unbounded storage in the controlled benchmark. M14
active experimentation is the recommended next milestone because it tests whether the agent can
create useful evidence rather than only manage evidence already supplied.

