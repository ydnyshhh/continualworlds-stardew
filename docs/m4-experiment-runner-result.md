# M4 experiment runner and resume result

## Result

M4 passed. The runner now creates a complete, auditable experiment from one frozen
YAML configuration and reconstructs current state from its authenticated event log.

The gate used a real normalized turn-and-move trajectory recorded by the M3 live
test. Two runs had different run IDs and request IDs but produced the same normalized
trajectory digest:

`3b067f930c672c233064094b6ecf6642ef84ac6ae1e1a21d648f7151172b79a1`

An independent third run published a combined checkpoint, recorded a task start,
transitioned to interrupted, restored the authenticated checkpoint, and completed
the task. Replaying the same completion added zero events.

| Check | Result |
| --- | --- |
| Independent equivalent runs | Passed |
| Actions per equivalent run | 2 and 2 |
| Trailing events after interrupted checkpoint | 4 |
| Recovered committed actions | 2 |
| Duplicate events after completion replay | 0 |
| Unique idempotency keys | Yes |
| Final lifecycle phase | `completed` |

## Run contract

- YAML input is parsed with `safe_load` and rejected if it contains unknown fields.
- Configuration and every nested model are frozen.
- The stable run ID includes suite, model, condition, seed, and configuration hash.
- `config.json` is atomically written and must match when a run directory reopens.
- `run_created` records code revision, dirty patch hash, runtime versions, provider,
  model, route, decoding settings, and the complete configuration.
- Lifecycle transitions are legal only according to the explicit state graph.
- Task recording writes before/after observations, policy decision, action starts and
  completions, score, outcome, exact artifact hash, and normalized trajectory hash.
- Stable operation keys make partial recording replayable and reject conflicting
  content under the same identity.
- Checkpoint resume validates the retained event prefix and frozen configuration
  before publishing recovery events.

## Evidence

- Configuration: `configs/m4-scripted.yaml`
- Machine-readable gate report: `runtime/smoke/m4-gate.json`
- Gate workspace: `runtime/m4-gate-v1`
- Automated verification: 70 tests passed.

The M4 gate reuses the M3 live trajectory and a synthetic save payload to isolate
runner recovery semantics. Live game save, crash, rollback, and reload behavior was
already validated by the M2 interruption gate.
