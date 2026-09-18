# M7 reflection, belief revision, and consolidation

## Result

M7 passed its deterministic and authenticated gates. On the hidden Moonroot crop-rule
fixture, the system formed a supported four-watered-night belief, encountered contrary
evidence, and created a five-watered-night successor. The first version remained in the
audit store as `superseded`; current retrieval returned only the active successor and
excluded a separately deleted belief.

## Implemented contract

- Evidence selection has a fixed token budget and deterministic priority for failure,
  surprise, outcome, and recency.
- Reflection output is a strict schema containing lessons, failed assumptions,
  counterfactuals, goals, beliefs, citations, confidence, and optional predictions.
- All cited event IDs must come from the selected evidence set.
- Belief revision requires an active parent and contradictory evidence. It creates a new
  immutable version and marks the parent superseded through append-only status history.
- Consolidation creates deterministic semantic memories from at least two episodes and
  preserves the union of their source event IDs.
- Refinement policies support nightly, failure-triggered, surprise-triggered, and
  agent-selected execution.
- Prediction outcomes are scored with Brier score and aggregate confidence statistics.

## Deterministic gate

The deterministic report is `runtime/smoke/m7-reflection-gate.json`. It verified:

- initial belief status `superseded` and revised belief status `active`;
- contradiction citation on the revised version;
- exclusion of deleted and superseded beliefs from current retrieval;
- semantic consolidation from two episodes with both source events;
- all four refinement policy triggers;
- two scored predictions with Brier score `0.32500000000000007`.

## Authenticated GLM 5.3 gate

The authenticated report is `runtime/smoke/m7-live-glm-5.3.json`. Prime Agent 0.9.5
ran two reflections in one direct RPC process through OpenRouter `z-ai/glm-5.3`.

| Measure | Result |
|---|---:|
| Model calls | 2 |
| Actual API route | `openai-completions` |
| Input tokens | 1,240 |
| Output tokens | 449 |
| Cost | $0.00397144 |
| Final lifecycle phase | `completed` |

The event log reopened successfully through `EventStore`, validating all 11 records,
their sequences, predecessor hashes, event hashes, and final cursor. Its final hash is
`13e71438b1bfc28a5507f8c21acb17f292a7c7e451d7b8a2f5a82135e17135e5`.

Prime's daemon relay withheld the completion frame on the first attempt even though the
model response was already saved. The passing run used Prime's documented direct worker
mode (`--no-session`); one RPC process remained alive across both calls, while the
PrimeStardew event store and SQLite memory database supplied durable experiment state.

## Scope

This is a controlled replay fixture for the M7 learning contract. It establishes belief
provenance, revision, consolidation, trigger behavior, and calibration mechanics. Longer
in-game learning curves and multi-seed comparisons remain part of M9 and M10.
