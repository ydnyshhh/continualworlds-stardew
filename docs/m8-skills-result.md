# M8 procedural skills and causal ablation

## Result

M8 passed its deterministic ablation and authenticated disposable-live gate. A watering
procedure was proposed from three successful M3 trajectories, compiled through the typed
environment action boundary, validated in replay and the isolated Stardew save, and executed
successfully three times at score 1.0.

## Skill contract

- Definitions contain an immutable ID and version, parameters and numeric bounds,
  preconditions, postconditions, typed steps, and every source trajectory ID.
- Model proposals require at least two successful trajectories and may cite only the supplied
  sources and their common fixture action allowlist.
- The compiler accepts only exact declared parameters. It rejects actions outside both the
  global typed-action set and the current fixture allowlist.
- Activation requires passing replay and disposable validation. Each result retains its stage,
  fixture, trajectory hash, score, success, and failure reasons.
- SQLite retains immutable versions and append-only lifecycle history. Rollback selects an
  earlier retained version without changing either version's content.
- Each execution records score, success, baseline/actual model decisions, and baseline/actual
  primitive actions.

## Deterministic D-versus-E ablation

The report is `runtime/smoke/m8-skills-gate.json`. Both conditions used the same three
live-recorded M3 watering trajectories.

| Measure | D: memory + retrieval | E: plus skills |
|---|---:|---:|
| Successful watering tasks | 3/3 | 3/3 |
| Mean task score | 1.0 | 1.0 |
| Model decisions/calls | 3 | 1 proposal |
| Primitive actions | 9 | 9 |
| Privileged actions | 0 | 0 |

The net gain was two model decisions after charging the proposal call. Primitive-action savings
were zero because proceduralization reused the same necessary `choose_item`, `turn`, and `use`
sequence. Version 2 was retained and activated during the rollback check; rollback restored
version 1 before scored use.

The baseline and skill event stores reopened as valid hash chains with 48 and 49 records.

## Authenticated disposable-live gate

The report is `runtime/smoke/m8-live-glm-5.3.json`. Prime Agent 0.9.5 used OpenRouter
`z-ai/glm-5.3` through the actual `openai-completions` route.

| Measure | Result |
|---|---:|
| Proposal model calls | 1 |
| Replay validation | Passed |
| Disposable live validation | Passed |
| Further scored live executions | 3/3 |
| Task scores | 1.0, 1.0, 1.0 |
| Net decisions saved after proposal | 2 |
| Primitive actions saved | 0 |
| Input tokens | 3,309 |
| Output tokens | 394 |
| Cost | $0.0063662 |

The live log contains 46 verified hash-chained events, ends in `completed`, and has final hash
`ab32a2ed8be7aacf5268eda15d73435f376aba52ce3c683ac6e022da89e9135b`.

The first live attempt demonstrated the activation guard by rejecting a skill whose disposable
score was below 1.0. The final executor waits for its declared watering postcondition before
capturing the scored state, handling the game's tool animation timing without bypassing the
normal `use` action.

## Scope

M8 establishes proceduralization, restricted execution, validation, rollback, and causal
efficiency accounting for one atomic watering skill. Skill composition and longer-horizon value
are evaluated by the M9 and M10 project and season benchmarks.
